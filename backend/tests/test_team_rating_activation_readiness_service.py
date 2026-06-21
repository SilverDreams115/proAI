"""R5.6-A: activation-readiness service (read-only, diagnostic only)."""
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
from app.services.team_rating_activation_readiness_service import (
    build_slate_activation_readiness,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'readiness.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id, matches, bucket, rating):
    return {
        "team_id": team_id, "namespace": "national", "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["national"]),
    }


def _pred(match_id, probs, *, sanity=None):
    return PredictionModel(
        match_id=match_id, generated_at=_BASE,
        home_probability=probs[0], draw_probability=probs[1], away_probability=probs[2],
        recommended_outcome="1", confidence_band="medium",
        sanity_audit_json=json.dumps(sanity) if sanity is not None else None,
    )


def _seed(session) -> ProgolSlateModel:
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {n: TeamModel(name=n, country=None) for n in "ABCDEFGH"}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, ghost, *teams.values()])
    session.flush()

    def _match(home, away, day):
        m = MatchModel(
            competition_id=friendly.id, home_team_id=home.id,
            away_team_id=away.id, kickoff_at=_BASE.replace(day=day),
        )
        session.add(m)
        session.flush()
        return m

    m_route = _match(teams["A"], teams["B"], 1)
    m_soft = _match(teams["C"], teams["D"], 2)
    m_review = _match(teams["E"], teams["F"], 3)
    m_hard = _match(teams["G"], teams["H"], 4)
    m_rating = _match(teams["A"], ghost, 5)
    slate = ProgolSlateModel(
        label="rd", draw_code="PG-RD", week_type="weekend",
        composition_hash="hash", slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m_route, m_soft, m_review, m_hard, m_rating), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(
        algorithm_version="elo_v1", config_json="{}", source_result_count=1,
        rated_match_count=1, excluded_match_count=0, input_checksum="in",
        output_checksum="out", status="computed",
    )
    repo.bulk_insert_snapshots(run.id, [
        _snap(teams["A"].id, 8, "medium", 1560.0),
        _snap(teams["B"].id, 10, "strong", 1490.0),
        _snap(teams["C"].id, 8, "medium", 1520.0),
        _snap(teams["D"].id, 9, "strong", 1495.0),
        _snap(teams["E"].id, 8, "medium", 1530.0),
        _snap(teams["F"].id, 9, "strong", 1480.0),
        _snap(teams["G"].id, 8, "medium", 1515.0),
        _snap(teams["H"].id, 9, "strong", 1500.0),
    ])
    repo.mark_run_active(run.id)
    session.add_all([
        _pred(m_route.id, (0.6, 0.25, 0.15)),
        _pred(m_soft.id, (0.45, 0.30, 0.25), sanity={"fallback_used": True}),
        _pred(m_review.id, (0.5, 0.3, 0.2), sanity={"final_status": "REVISAR"}),
        _pred(m_hard.id, (0.5, 0.3, 0.2), sanity={"final_status": "BLOCKED"}),
        _pred(m_rating.id, (0.4, 0.35, 0.25)),
    ])
    session.commit()
    return slate


def test_readiness_not_ready_while_flags_off(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    r = build_slate_activation_readiness(session, slate)

    assert r.mode == "activation_readiness"
    assert r.production_active is False
    assert r.ready_for_canary is False
    assert r.ready_for_full_activation is False
    assert r.dry_run_summary.total_matches == 5
    assert r.dry_run_summary.would_route == 2
    assert r.dry_run_summary.changed_top_pick_count == 0
    session.close()


def test_calibrator_approved_inactive(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    r = build_slate_activation_readiness(session, slate)

    assert r.calibrator.approval_status == "approved_inactive"
    assert r.calibrator.approved_for_canary is True
    assert r.calibrator.productive_available is False
    assert r.calibrator.active is False
    # The approval does not pretend to be productive.
    statuses = {c.check: c.status for c in r.readiness_checks}
    assert statuses["calibrator_approved_inactive"] == "pass"
    assert statuses["calibrator_productive_available"] == "blocking_until_full_activation"
    assert statuses["feature_flag_off"] == "blocking_until_canary"
    assert statuses["read_only_guards"] == "pass"
    session.close()


def test_canary_plan_positions_and_blockers(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    r = build_slate_activation_readiness(session, slate)

    assert r.canary_plan.canary_allowed_matches == [1, 2]
    assert r.canary_plan.blocked_matches == [3, 4, 5]
    counts = {c.check: c.count for c in r.readiness_checks}
    assert counts["rating_coverage"] == 1            # m_rating
    assert counts["review_blockers_present"] >= 1    # m_review
    assert counts["hard_sanity_blockers_present"] >= 1  # m_hard
    # Rollback plan is present and concrete.
    assert len(r.canary_plan.rollback) >= 3
    assert any("team_rating_gate_enabled=false" in step for step in r.canary_plan.rollback)
    session.close()


def test_target_activation_policy(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    r = build_slate_activation_readiness(session, slate)
    t = r.target_activation
    assert t.scope == "minimal_canary"
    assert t.routing_policy == "rating_replaces_fallback"
    assert t.calibrator_id == "international_friendlies_temperature_v1"
    assert t.temperature == 2.22
    assert t.review_blocks is True
    assert t.hard_blockers_block is True
    session.close()


def test_readiness_does_not_write(tmp_path):
    from app.models.tables import MatchFeatureSnapshotModel
    from app.models.tables import TicketRecommendationSnapshotModel

    session = _make_session(tmp_path)
    slate = _seed(session)
    before = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }

    build_slate_activation_readiness(session, slate)

    after = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    assert before == after
    assert not session.new and not session.dirty
    session.rollback()
    session.close()


def test_no_active_prediction_or_ticket_service_import():
    from pathlib import Path

    src = Path(
        "backend/app/services/team_rating_activation_readiness_service.py"
    ).read_text()
    code = "\n".join(
        line for line in src.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "*"))
    )
    assert "import PredictionService" not in code
    assert "import TicketRecommendationService" not in code
    assert "prediction_service" not in code
    assert "ticket_recommendation_service" not in code
    assert "build_match_features" not in code
    assert "save_snapshot" not in code
    assert "session.add" not in code
    assert ".commit(" not in code
