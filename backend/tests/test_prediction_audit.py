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
