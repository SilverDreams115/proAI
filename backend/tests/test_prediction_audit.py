"""Persistence regression for the prediction audit log.

Phase 1 item #4: every prediction the service computes should leave a
row in the predictions table, including the rationale that produced
the band (competition readiness, blocked_reason, anchors snapshot).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "audit.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_slate(session) -> str:
    from app.models.tables import (
        CompetitionModel,
        MatchModel,
        ProgolSlateMatchModel,
        ProgolSlateModel,
        TeamModel,
    )

    comp = CompetitionModel(name="Friendlies International", country="World", season="2026")
    home = TeamModel(name="Mexico", country="MX")
    away = TeamModel(name="Australia", country="AU")
    session.add_all([comp, home, away])
    session.flush()
    kickoff = datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc)
    match = MatchModel(
        competition=comp, home_team=home, away_team=away, kickoff_at=kickoff
    )
    session.add(match)
    session.flush()
    slate = ProgolSlateModel(
        label="Test", draw_code="PG-TEST", week_type="weekend",
        registration_closes_at=kickoff - timedelta(hours=6), is_archived=False,
        composition_hash="hash-pgtest", slate_version=1,
    )
    session.add(slate)
    session.flush()
    session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=1))
    session.flush()
    return slate.id


def test_prediction_run_persists_audit_row(tmp_path) -> None:
    from sqlalchemy import select

    from app.models.tables import PredictionModel, ProgolSlateModel
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate_id = _seed_slate(session)
        slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.id == slate_id))
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        service = PredictionService(training)
        responses = service.build_slate_predictions(slate)
        assert len(responses) == 1
        session.flush()

        rows = session.scalars(select(PredictionModel)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.confidence_band == responses[0].confidence_band
        assert row.competition_readiness == responses[0].competition_readiness
        # No data was seeded for these teams, so the anchored data
        # gate must fire and persist the reason.
        assert row.blocked_reason in {"insufficient_data_anchors", "unclassified_competition"}
        # anchors_json is valid JSON and contains the four counters.
        import json
        anchors = json.loads(row.anchors_json)
        assert set(anchors) >= {
            "home_recent_matches",
            "away_recent_matches",
            "head_to_head_matches",
            "evidence_count",
        }
    finally:
        invalidate_slate_prediction_cache()
        session.close()


def test_audit_persists_full_sanity_trace_with_raw_and_decision(tmp_path) -> None:
    """v18: the audit row carries the complete guardrail trace and keeps
    raw AND decision vectors so a partido can be explained after the fact."""
    import json

    from sqlalchemy import select

    from app.models.tables import PredictionModel, ProgolSlateModel
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )
    from app.services.sanity_service import SANITY_POLICY_VERSION

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate_id = _seed_slate(session)
        slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.id == slate_id))
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        service = PredictionService(training)
        service.build_slate_predictions(slate)
        session.flush()

        row = session.scalars(select(PredictionModel)).all()[0]
        # The model-adjusted columns (backtesting source) are still written.
        assert row.home_probability is not None
        assert row.draw_probability is not None
        assert row.away_probability is not None
        # The full sanity trace is present and self-describing.
        assert row.sanity_audit_json is not None
        trace = json.loads(row.sanity_audit_json)
        assert set(trace) >= {
            "raw_probabilities",
            "display_probabilities",
            "decision_probabilities",
            "optimizer_probabilities",
            "sanity_flags",
            "risk_level",
            "evidence_level",
            "final_status",
            "sanity_policy_version",
            "model_artifact_id",
            "fallback_used",
            "is_international_friendly",
        }
        # Raw and decision are BOTH stored — raw is never replaced.
        assert set(trace["raw_probabilities"]) == {"L", "E", "V"}
        assert set(trace["decision_probabilities"]) == {"L", "E", "V"}
        # optimizer == decision by construction (optimizer reads decision).
        assert trace["optimizer_probabilities"] == trace["decision_probabilities"]
        assert trace["sanity_policy_version"] == SANITY_POLICY_VERSION
        assert isinstance(trace["fallback_used"], bool)
    finally:
        invalidate_slate_prediction_cache()
        session.close()


def test_audit_decision_degrades_raw_for_extreme_friendly(tmp_path) -> None:
    """The headline audit use case: raw V high, decision V capped, with the
    flags that explain why — recoverable entirely from the audit row."""
    import json

    from sqlalchemy import select

    from app.models.tables import PredictionModel, ProgolSlateModel
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate_id = _seed_slate(session)
        slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.id == slate_id))
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        service = PredictionService(training)

        # Force an extreme raw away-favourite so the guardrail must degrade
        # it; the seeded friendly has no data so evidence is LOW.
        def _fake_score(match):
            return {"home": 0.02, "draw": 0.17, "away": 0.81}

        training.score_match = _fake_score  # type: ignore[assignment]
        service.build_slate_predictions(slate)
        session.flush()

        row = session.scalars(select(PredictionModel)).all()[0]
        trace = json.loads(row.sanity_audit_json)
        raw_v = trace["raw_probabilities"]["V"]
        decision_v = trace["decision_probabilities"]["V"]
        assert raw_v >= 0.75
        assert decision_v <= 0.65
        assert decision_v < raw_v
        assert row.away_probability == decision_v
        assert "LOW_EVIDENCE" in trace["sanity_flags"]
        assert trace["final_status"] in {"REVISAR", "BLOQUEADO"}
    finally:
        invalidate_slate_prediction_cache()
        session.close()


def _load_diagnostic_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "slate_diagnostic_report.py"
    spec = importlib.util.spec_from_file_location("slate_diagnostic_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_diagnostic_report_reads_audit_from_db(tmp_path) -> None:
    """Requirement 4: the diagnostic report can reconstruct the per-match
    raw/display/decision view from the persisted audit rows."""
    from sqlalchemy import select

    from app.models.tables import ProgolSlateModel
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate_id = _seed_slate(session)
        slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.id == slate_id))
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        training.score_match = lambda match: {"home": 0.02, "draw": 0.17, "away": 0.81}  # type: ignore[assignment]
        PredictionService(training).build_slate_predictions(slate)
        session.commit()

        module = _load_diagnostic_module()
        rows = module._load_predictions_from_db(slate_id)
        assert len(rows) == 1
        row = rows[0]
        # Raw and decision both reconstructed, raw preserved unchanged.
        assert row["raw_probabilities"]["V"] >= 0.75
        assert row["decision_probabilities"]["V"] <= 0.65
        assert row["optimizer_probabilities"] == row["decision_probabilities"]
        assert "LOW_EVIDENCE" in row["flags"]
        assert row["home_team_name"] == "Mexico"
        assert row["away_team_name"] == "Australia"
    finally:
        invalidate_slate_prediction_cache()
        session.close()


def test_old_rows_without_sanity_trace_stay_null(tmp_path) -> None:
    """Requirement 5 backfill safety: a legacy audit row that predates the
    sanity trace must keep raw/decision NULL rather than inventing a
    decision, and its model probabilities must stay untouched."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.tables import PredictionModel, ProgolSlateModel

    session = _make_session(tmp_path)
    try:
        slate_id = _seed_slate(session)
        slate = session.scalar(select(ProgolSlateModel).where(ProgolSlateModel.id == slate_id))
        match_id = slate.matches[0].match_id

        # Simulate a pre-v18 row: model probabilities present, no trace.
        session.add(
            PredictionModel(
                match_id=match_id,
                slate_id=slate_id,
                generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                home_probability=0.4,
                draw_probability=0.3,
                away_probability=0.3,
                recommended_outcome="1",
                confidence_band="low",
                competition_readiness="context_only",
                anchors_json="{}",
                sanity_audit_json=None,
            )
        )
        session.commit()

        module = _load_diagnostic_module()
        rows = module._load_predictions_from_db(slate_id)
        legacy = next(r for r in rows if r["raw_probabilities"] is None)
        # No decision invented for the legacy row.
        assert legacy["raw_probabilities"] is None
        assert legacy["decision_probabilities"] is None
        # The model probabilities are untouched on disk.
        db_row = session.scalars(
            select(PredictionModel).where(PredictionModel.sanity_audit_json.is_(None))
        ).all()[0]
        assert db_row.home_probability == 0.4
        assert db_row.sanity_audit_json is None
    finally:
        session.close()
