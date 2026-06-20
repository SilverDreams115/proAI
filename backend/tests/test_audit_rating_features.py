"""R3: read-only rating-feature auditor.

Covers full/partial/no_rating classification, both_medium_plus, rating_diff
only when both sides present, missing-rating reporting, latest-active-run
usage, per-competition grouping, flag-off safety, no-DB-writes, and that the
production services do not integrate the rating helper.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.repositories.team_rating_repository import TeamRatingRepository
from scripts import audit_rating_features as auditor


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'audit.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _team(session, name, placeholder=False):
    t = TeamModel(name=name, country=None, is_placeholder=placeholder)
    session.add(t)
    session.flush()
    return t


def _comp(session, name):
    c = CompetitionModel(name=name, country="X", season="2026")
    session.add(c)
    session.flush()
    return c


def _match(session, comp, home, away, day):
    m = MatchModel(
        competition_id=comp.id, home_team_id=home.id, away_team_id=away.id,
        kickoff_at=datetime(2026, 1, day, tzinfo=timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _snap(team_id, namespace, rating, matches, bucket):
    return {
        "team_id": team_id, "namespace": namespace, "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["X"]),
    }


def _seed(session):
    """Build a 3-position friendly slate plus an active run.

    pos1: both medium+ (full_rating, both_medium_plus)
    pos2: one side weak (full_rating, NOT both_medium_plus)
    pos3: one side placeholder with no snapshot (partial_rating)
    """
    friendly = _comp(session, "International Friendlies")
    a = _team(session, "Brazil")
    b = _team(session, "Argentina")
    cc = _team(session, "Spain")
    d = _team(session, "Tinyland")          # weak rating
    e = _team(session, "Realteam")
    ghost = _team(session, "GhostNation", placeholder=True)  # no snapshot

    m1 = _match(session, friendly, a, b, 1)
    m2 = _match(session, friendly, cc, d, 2)
    m3 = _match(session, friendly, e, ghost, 3)

    slate = ProgolSlateModel(
        label="Test", draw_code="PG-TEST", week_type="weekend",
        composition_hash="hash-test", slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2, m3), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(
        algorithm_version="elo_v1", config_json="{}", source_result_count=10,
        rated_match_count=10, excluded_match_count=0,
        input_checksum="in", output_checksum="out", status="computed",
    )
    repo.bulk_insert_snapshots(run.id, [
        _snap(a.id, "national", 1700.0, 12, "strong"),
        _snap(b.id, "national", 1500.0, 8, "medium"),
        _snap(cc.id, "national", 1600.0, 6, "medium"),
        _snap(d.id, "national", 1450.0, 2, "weak"),   # weak side
        _snap(e.id, "national", 1550.0, 5, "medium"),
        # ghost: no snapshot at all
    ])
    repo.mark_run_active(run.id)
    session.commit()
    return run


def test_slate_audit_full_partial_no_rating(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    _run, snaps = auditor.load_active_run_snapshots(session)
    report = auditor.audit_slate(session, snaps, "PG-TEST")
    rows = {r["position"]: r for r in report["rows"]}

    # pos1: both medium+ → full_rating, both_medium_plus, rating_diff present
    assert rows[1]["status"] == "full_rating"
    assert rows[1]["both_rating_medium_plus"] is True
    assert rows[1]["rating_diff"] == 200.0
    assert rows[1]["rating_namespace"] == "national"

    # pos2: weak side → full_rating but NOT both_medium_plus
    assert rows[2]["status"] == "full_rating"
    assert rows[2]["both_rating_medium_plus"] is False

    # pos3: placeholder side has no snapshot → partial_rating, no fabricated diff
    assert rows[3]["status"] == "partial_rating"
    assert rows[3]["rating_present"] is False
    assert rows[3]["rating_diff"] is None
    assert rows[3]["away_matches_count"] == 0

    s = report["summary"]
    assert s["total_matches"] == 3
    assert s["full_rating_count"] == 2
    assert s["partial_rating_count"] == 1
    assert s["no_rating_count"] == 0
    assert s["both_medium_plus_count"] == 1
    assert s["positions_missing_rating"] == [3]
    session.close()


def test_competition_grouping(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    _run, snaps = auditor.load_active_run_snapshots(session)
    comps = auditor.audit_competitions(session, snaps, competition=None, active_only=True)
    by_name = {c["competition"]: c for c in comps}

    assert "International Friendlies" in by_name
    fr = by_name["International Friendlies"]
    assert fr["slate_match_count"] == 3
    assert fr["partial_rating_count"] == 1
    assert fr["no_rating_count"] == 0
    # 1 of 3 positions has both_medium_plus → thin coverage, not a candidate.
    assert fr["both_medium_plus_rate"] < 0.8
    assert fr["candidate_for_backtest"] is False
    assert "thin_rating_coverage" in fr["blocker"]
    session.close()


def test_auditor_uses_latest_active_run(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    run, snaps = auditor.load_active_run_snapshots(session)
    assert run.status == "active"
    assert run.algorithm_version == "elo_v1"
    assert len(snaps) == 5  # 5 snapshots seeded (ghost excluded)
    session.close()


def test_auditor_writes_nothing(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    from app.models.team_rating import TeamRatingSnapshotModel

    before = session.query(TeamRatingSnapshotModel).count()
    _run, snaps = auditor.load_active_run_snapshots(session)
    auditor.audit_slate(session, snaps, "PG-TEST")
    auditor.audit_competitions(session, snaps, competition=None, active_only=True)

    assert not session.new
    assert not session.dirty
    assert session.query(TeamRatingSnapshotModel).count() == before
    session.rollback()
    session.close()


def test_flag_off_blocks_active_load():
    from app.services import team_rating_feature_service as svc

    assert svc.rating_features_enabled() is False
    sentinel = object()
    assert svc.load_rating_features(
        sentinel, "h", "a", namespace="national"  # type: ignore[arg-type]
    ) is None


def test_production_services_do_not_integrate_rating_helper():
    # The productive feature/prediction services must not import or call the
    # rating helper — no active integration while the flag is off.
    import app.services.feature_service as fs
    import app.services.prediction_service as ps

    for module in (fs, ps):
        with open(module.__file__) as fh:
            text = fh.read()
        assert "team_rating_feature_service" not in text
        assert "build_rating_features" not in text
        assert "load_rating_features" not in text
