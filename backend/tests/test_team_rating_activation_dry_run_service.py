"""R5.5: controlled-activation dry-run service (read-only, diagnostic only)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.repositories.team_rating_repository import TeamRatingRepository
from app.services.team_rating_activation_dry_run_service import (
    build_slate_activation_dry_run,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'dryrun.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id: str, matches: int, bucket: str, rating: float):
    return {
        "team_id": team_id,
        "namespace": "national",
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
        "competitions_seen_json": json.dumps(["national"]),
    }


def _pred(match_id, probs, *, sanity=None):
    return PredictionModel(
        match_id=match_id,
        generated_at=_BASE,
        home_probability=probs[0],
        draw_probability=probs[1],
        away_probability=probs[2],
        recommended_outcome="1",
        confidence_band="medium",
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
            competition_id=friendly.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=_BASE.replace(day=day),
        )
        session.add(m)
        session.flush()
        return m

    m_route = _match(teams["A"], teams["B"], 1)        # clean -> routes
    m_soft = _match(teams["C"], teams["D"], 2)         # FALLBACK_USED -> routes
    m_review = _match(teams["E"], teams["F"], 3)       # REVISAR -> blocked
    m_hard = _match(teams["G"], teams["H"], 4)         # BLOCKED -> blocked
    m_rating = _match(teams["A"], ghost, 5)            # partial rating -> blocked

    slate = ProgolSlateModel(
        label="dryrun", draw_code="PG-DRYRUN", week_type="weekend",
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
    repo.bulk_insert_snapshots(
        run.id,
        [
            _snap(teams["A"].id, 8, "medium", 1560.0),
            _snap(teams["B"].id, 10, "strong", 1490.0),
            _snap(teams["C"].id, 8, "medium", 1520.0),
            _snap(teams["D"].id, 9, "strong", 1495.0),
            _snap(teams["E"].id, 8, "medium", 1530.0),
            _snap(teams["F"].id, 9, "strong", 1480.0),
            _snap(teams["G"].id, 8, "medium", 1515.0),
            _snap(teams["H"].id, 9, "strong", 1500.0),
            # Ghost (away of m_rating) intentionally has no snapshot.
        ],
    )
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


def test_dry_run_summary_breakdown(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    report = build_slate_activation_dry_run(session, slate)

    assert report.mode == "activation_dry_run"
    assert report.production_active is False
    assert report.safe_to_activate is False
    assert report.dry_run_probability_model == "international_friendlies_temperature_v1"

    s = report.summary
    assert s.total_matches == 5
    assert s.eligible_if_enabled == 4          # m_route, m_soft, m_review, m_hard
    assert s.would_route == 2                  # m_route, m_soft
    assert s.would_keep_current == 3
    assert s.blocked_by_rating == 1            # m_rating
    assert s.blocked_by_review >= 1            # m_review
    assert s.blocked_by_hard_sanity >= 1       # m_hard
    assert s.positions_would_route == [1, 2]
    session.close()


def test_activation_blockers_and_policy(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    report = build_slate_activation_dry_run(session, slate)

    assert "feature_flag_off" in report.activation_blockers
    assert "calibrator_productive_available_false" in report.activation_blockers
    assert report.calibrator is not None
    assert report.calibrator.productive_available is False
    assert report.calibrator.compatible is True
    assert report.activation_policy.routing_policy == "rating_replaces_fallback"
    assert report.activation_policy.temperature == 2.22
    assert report.activation_policy.review_blocks is True
    session.close()


def test_routed_match_simulated_probabilities(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    report = build_slate_activation_dry_run(session, slate)
    routed = next(m for m in report.matches if m.position == 1)

    assert routed.would_route is True
    assert routed.dry_run_engine == "team_rating_calibrated"
    assert routed.current_engine == "xgboost"
    # Probabilities stay valid distributions.
    assert routed.current_probabilities is not None
    assert routed.dry_run_probabilities is not None
    assert sum(routed.current_probabilities.values()) == pytest.approx(1.0, abs=1e-6)
    assert sum(routed.dry_run_probabilities.values()) == pytest.approx(1.0, abs=1e-3)
    # Deltas are coherent (sum to ~0) and there is a real change.
    assert sum(routed.probability_delta.values()) == pytest.approx(0.0, abs=1e-6)
    assert routed.max_abs_delta > 0.0
    # Temperature scaling is monotonic: the top pick does not flip.
    assert routed.top_pick_changed is False
    assert "dry_run_only" in routed.warnings
    session.close()


def test_temperature_scaling_does_not_change_picks(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)

    report = build_slate_activation_dry_run(session, slate)
    assert report.summary.changed_top_pick_count == 0
    assert report.summary.positions_changed_pick == []
    # Soft-blocked match also routes (rating replaces FALLBACK_USED).
    soft = next(m for m in report.matches if m.position == 2)
    assert soft.would_route is True
    assert soft.current_engine == "fallback"
    # Rating-blocked match keeps current engine and does not route.
    rating_blocked = next(m for m in report.matches if m.position == 5)
    assert rating_blocked.would_route is False
    assert rating_blocked.dry_run_engine == rating_blocked.current_engine
    assert rating_blocked.probability_delta == {"1": 0.0, "X": 0.0, "2": 0.0}
    session.close()


def test_dry_run_does_not_write(tmp_path):
    from app.models.tables import MatchFeatureSnapshotModel
    from app.models.tables import TicketRecommendationSnapshotModel

    session = _make_session(tmp_path)
    slate = _seed(session)
    before = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }

    build_slate_activation_dry_run(session, slate)

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
    """The dry-run must not import or invoke the productive prediction / ticket
    services, nor any persisting feature path. We check executable lines only,
    so the explanatory docstring does not trigger false positives."""
    from pathlib import Path

    src = Path(
        "backend/app/services/team_rating_activation_dry_run_service.py"
    ).read_text()
    code_lines = [
        line
        for line in src.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "*"))
    ]
    code = "\n".join(code_lines)
    # No imports of the productive services.
    assert "import PredictionService" not in code
    assert "import TicketRecommendationService" not in code
    assert "prediction_service" not in code
    assert "ticket_recommendation_service" not in code
    # No persisting feature path and no snapshot writes.
    assert "build_match_features" not in code
    assert "save_snapshot" not in code
    assert "session.add" not in code
    assert ".commit(" not in code
