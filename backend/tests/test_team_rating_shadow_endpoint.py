"""R5.4: read-only Team Rating Shadow report service + endpoint.

The report must be a faithful shadow projection (gate currently OFF, what-if if
enabled) that never writes a row and never changes predictions / picks /
tickets / probabilities.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.models.tables import TicketRecommendationSnapshotModel
from app.repositories.team_rating_repository import TeamRatingRepository
from app.services.team_rating_shadow_report import build_slate_shadow_report


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'shadow_endpoint.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id: str, ns: str, matches: int, bucket: str, rating: float = 1500.0):
    return {
        "team_id": team_id,
        "namespace": ns,
        "rating": rating,
        "rating_delta": 0.0,
        "matches_count": matches,
        "wins": matches,
        "draws": 0,
        "losses": 0,
        "goals_for": matches,
        "goals_against": 0,
        "confidence_bucket": bucket,
        "last_result_at": None,
        "competitions_seen_json": json.dumps([ns]),
    }


def _seed_friendly_slate(session) -> ProgolSlateModel:
    """All-International-Friendlies slate: three rated matches plus one with a
    placeholder away team (partial / no rating) that mirrors PG-2338 pos13."""
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {
        name: TeamModel(name=name, country=None)
        for name in ("A", "B", "C", "D", "E", "F")
    }
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, ghost, *teams.values()])
    session.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _match(home, away, day):
        match = MatchModel(
            competition_id=friendly.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=base.replace(day=day),
        )
        session.add(match)
        session.flush()
        return match

    m1 = _match(teams["A"], teams["B"], 1)
    m2 = _match(teams["C"], teams["D"], 2)
    m3 = _match(teams["E"], teams["F"], 3)
    m_partial = _match(teams["A"], ghost, 4)  # partial: away has no rating

    slate = ProgolSlateModel(
        label="shadow-ep",
        draw_code="PG-SHADOW-EP",
        week_type="weekend",
        composition_hash="hash-ep",
        slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, match in enumerate((m1, m2, m3, m_partial), start=1):
        session.add(
            ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=pos)
        )

    repo = TeamRatingRepository(session)
    run = repo.create_run(
        algorithm_version="elo_v1",
        config_json="{}",
        source_result_count=1,
        rated_match_count=1,
        excluded_match_count=0,
        input_checksum="in",
        output_checksum="out",
        status="computed",
    )
    repo.bulk_insert_snapshots(
        run.id,
        [
            _snap(teams["A"].id, "national", 8, "medium", 1550.0),
            _snap(teams["B"].id, "national", 10, "strong", 1495.0),
            _snap(teams["C"].id, "national", 8, "medium", 1520.0),
            _snap(teams["D"].id, "national", 9, "strong", 1490.0),
            _snap(teams["E"].id, "national", 7, "medium", 1530.0),
            _snap(teams["F"].id, "national", 9, "strong", 1480.0),
            # Ghost (away of m_partial) intentionally has no snapshot.
        ],
    )
    repo.mark_run_active(run.id)
    session.commit()
    return slate


def test_shadow_report_current_off_with_if_enabled_breakdown(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed_friendly_slate(session)

    report = build_slate_shadow_report(session, slate)

    # Current gate is OFF: nothing eligible now, flags off, shadow-only mode.
    assert report.mode == "shadow_only"
    assert report.production_active is False
    assert report.feature_flag_enabled is False
    assert report.gate_flag_enabled is False
    assert report.routing_policy == "rating_replaces_fallback"
    assert report.summary.eligible_current == 0

    # If the gate were enabled, the three rated friendlies clear the guard and
    # the partial-rating match stays blocked.
    assert report.summary.total_matches == 4
    assert report.summary.eligible_if_enabled == 3
    assert report.summary.blocked_by_rating == 1
    assert report.summary.positions_eligible_if_enabled == [1, 2, 3]

    # Active run + calibrator candidate surfaced.
    assert report.active_rating_run.algorithm_version == "elo_v1"
    assert report.calibrator_candidate is not None
    assert report.calibrator_candidate.id == "international_friendlies_temperature_v1"
    assert report.calibrator_candidate.productive_available is False
    assert report.calibrator_candidate.compatible is True

    session.close()


def test_shadow_report_partial_rating_match_blocked(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed_friendly_slate(session)

    report = build_slate_shadow_report(session, slate)
    partial = next(m for m in report.matches if m.position == 4)

    assert partial.rating_status in {"partial_rating", "no_rating"}
    assert partial.eligible_if_enabled is False
    assert partial.would_use_rating_model_if_enabled is False
    assert "not_both_medium_plus" in partial.blockers


def test_shadow_report_does_not_write(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed_friendly_slate(session)
    before = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }

    build_slate_shadow_report(session, slate)

    after = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    assert before == after
    assert not session.new and not session.dirty
    session.rollback()
    session.close()


@pytest.mark.anyio
async def test_shadow_endpoint_200_and_read_only(client) -> None:
    from app.db import session as db_mod

    engine = db_mod.engine
    with Session(engine) as session:
        slate = _seed_friendly_slate(session)
        slate_id = slate.id

    def _snap_count() -> int:
        with Session(engine) as s:
            return s.scalar(
                select(func.count()).select_from(MatchFeatureSnapshotModel)
            ) or 0

    before = _snap_count()
    resp = await client.get(f"/api/predictions/slates/{slate_id}/team-rating-shadow")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "shadow_only"
    assert body["gate_flag_enabled"] is False
    assert body["summary"]["eligible_current"] == 0
    assert body["summary"]["eligible_if_enabled"] == 3
    assert body["calibrator_candidate"]["id"] == "international_friendlies_temperature_v1"
    assert len(body["matches"]) == 4
    # GET must not grow any persisted table.
    assert _snap_count() == before

    missing = await client.get(
        "/api/predictions/slates/does-not-exist/team-rating-shadow"
    )
    assert missing.status_code == 404
