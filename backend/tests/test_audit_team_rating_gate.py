"""R5.0: dry-run gate auditor — read-only, correct routing breakdown."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.repositories.team_rating_repository import TeamRatingRepository
from scripts import audit_team_rating_gate as gate_audit
from scripts.audit_rating_features import load_active_run_snapshots


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'gate.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id, ns, matches, bucket, rating=1500.0):
    return {
        "team_id": team_id, "namespace": ns, "rating": rating, "rating_delta": 0.0,
        "matches_count": matches, "wins": matches, "draws": 0, "losses": 0,
        "goals_for": matches, "goals_against": 0, "confidence_bucket": bucket,
        "last_result_at": None, "competitions_seen_json": json.dumps([ns]),
    }


def _seed(session):
    friendly = CompetitionModel(name="International Friendlies", country="World")
    libert = CompetitionModel(name="Copa Libertadores", country="SA")
    teams = {n: TeamModel(name=n, country=None) for n in ("A", "B", "C", "D", "E", "F")}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, libert, ghost, *teams.values()])
    session.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _match(comp, h, a, day):
        m = MatchModel(competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
                       kickoff_at=base.replace(day=day))
        session.add(m)
        session.flush()
        return m

    m_ok = _match(friendly, teams["A"], teams["B"], 1)      # both medium+ → guard eligible
    m_partial = _match(friendly, teams["C"], ghost, 2)       # one side no rating → blocked
    m_libert = _match(libert, teams["E"], teams["F"], 3)     # right ratings, wrong competition
    slate = ProgolSlateModel(label="t", draw_code="PG-T", week_type="weekend",
                             composition_hash="h", slate_version=1)
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m_ok, m_partial, m_libert), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(algorithm_version="elo_v1", config_json="{}", source_result_count=1,
                          rated_match_count=1, excluded_match_count=0,
                          input_checksum="i", output_checksum="o", status="computed")
    repo.bulk_insert_snapshots(run.id, [
        _snap(teams["A"].id, "national", 8, "medium"),
        _snap(teams["B"].id, "national", 10, "strong"),
        _snap(teams["C"].id, "national", 6, "medium"),   # ghost has none → partial
        _snap(teams["E"].id, "club", 12, "strong"),
        _snap(teams["F"].id, "club", 9, "medium"),
    ])
    repo.mark_run_active(run.id)

    # a legacy sanity-flagged prediction on the eligible friendly match
    session.add(PredictionModel(
        match_id=m_ok.id, generated_at=base, home_probability=0.4, draw_probability=0.3,
        away_probability=0.3, recommended_outcome="1", confidence_band="medium",
        sanity_audit_json=json.dumps({"sanity_flags": ["FALLBACK_USED"], "fallback_used": True}),
    ))
    session.commit()
    return slate


def test_gate_audit_breakdown(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _run, snaps = load_active_run_snapshots(session)
    links = sorted(slate.matches, key=lambda lnk: lnk.position)
    report = gate_audit.audit_matches(
        session, snaps, links, gate_competitions={"international friendlies"}
    )
    s = report["summary"]
    assert s["gate_enabled"] is False
    assert s["eligible_current"] == 0           # flag off
    assert s["blocked_by_flag"] == 3            # all matches blocked by flag today
    # rating-guard-only: only the both-medium-plus friendly clears
    assert s["eligible_if_enabled_rating_guard"] == 1
    assert s["blocked_by_competition"] == 1     # Copa Libertadores
    assert s["blocked_by_not_both_medium_plus"] == 1  # partial friendly
    # the one guard-eligible match carries a legacy FALLBACK_USED → full gate holds it
    assert s["blocked_by_sanity"] == 1
    assert s["would_route_to_rating_model"] == 0
    assert s["would_remain_fallback"] == 3
    # calibrator unavailable today → guard passer still held back
    assert s["blocked_by_missing_calibrator_today"] == 1
    session.close()


def test_gate_audit_writes_nothing(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _run, snaps = load_active_run_snapshots(session)
    links = list(slate.matches)
    before = session.query(PredictionModel).count()
    gate_audit.audit_matches(session, snaps, links, gate_competitions={"international friendlies"})
    assert not session.new and not session.dirty
    assert session.query(PredictionModel).count() == before
    session.rollback()
    session.close()
