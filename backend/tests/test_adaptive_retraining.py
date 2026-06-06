"""Tests for AdaptiveRetrainingService.

Covers:
  - readiness=False when trainable rows < threshold
  - readiness=False when complete slates < threshold
  - readiness=False when conflict_rate exceeds threshold
  - dry_run does NOT create a ModelTrainingRunModel
  - dry_run recommends skip/recalibrate/full based on thresholds
  - run_retraining_if_ready blocks (success=False) when not ready
  - run_retraining_if_ready creates a ModelTrainingRunModel when ready
  - No conflicting results leak into trainable rows
  - No slate_id=None predictions appear in trainable rows
  - Previous ModelTrainingRunModel is NOT deleted after retrain
  - rollback_run_id points to the pre-retrain run
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchResultModel,
    ModelTrainingRunModel,
    PredictionModel,
    ProgolJornadaScoreModel,
    SourceModel,
)
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.adaptive_retraining import RetrainingThresholds
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.adaptive_retraining_service import AdaptiveRetrainingService
from app.services.jornada_scoring_service import JornadaScoringService


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'retrain_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _source(session: Session, name: str = "retrain-src", priority: int = 50) -> SourceModel:
    from sqlalchemy import text

    existing = session.execute(
        text("SELECT id FROM sources WHERE name = :n"), {"n": name}
    ).scalar_one_or_none()
    if existing:
        return session.get(SourceModel, existing)
    src = SourceModel(
        name=name,
        base_url="http://test",
        kind="thesportsdb_season",
        parser_profile="generic",
        result_source_priority=priority,
    )
    session.add(src)
    session.flush()
    return src


def _make_slate(session: Session, draw_code: str = "PG-RT-1", n: int = 3) -> Any:
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="Liga MX"),
            home_team=TeamPayload(name=f"HTm{draw_code}-{i}"),
            away_team=TeamPayload(name=f"ATm{draw_code}-{i}"),
            kickoff_at=now + timedelta(days=10),
        )
        for i in range(1, n + 1)
    ]
    slate = SlateRepository(session).upsert_slate(
        ProgolSlateCreate(
            label=f"Test {draw_code}",
            draw_code=draw_code,
            week_type="weekend",
            registration_closes_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            matches=matches,
        )
    )
    session.flush()
    return slate


def _add_prediction(
    session: Session,
    match_id: str,
    slate_id: str | None,
    composition_hash: str | None,
    *,
    recommended_outcome: str = "1",
    confidence_band: str = "medium",
) -> PredictionModel:
    pred = PredictionModel(
        match_id=match_id,
        slate_id=slate_id,
        composition_hash=composition_hash,
        slate_version=1,
        generated_at=datetime.now(timezone.utc),
        home_probability=0.6,
        draw_probability=0.25,
        away_probability=0.15,
        recommended_outcome=recommended_outcome,
        confidence_band=confidence_band,
        anchors_json="{}",
    )
    session.add(pred)
    session.flush()
    return pred


def _add_result(
    session: Session,
    match_id: str,
    source_id: str,
    result_code: str = "1",
) -> MatchResultModel:
    r = MatchResultModel(
        match_id=match_id,
        source_id=source_id,
        played_at=datetime.now(timezone.utc),
        home_goals=1 if result_code == "1" else 0,
        away_goals=1 if result_code == "2" else 0,
        result_code=result_code,
    )
    session.add(r)
    session.flush()
    return r


def _score_slate(session: Session, slate: Any) -> ProgolJornadaScoreModel:
    score = JornadaScoringService(session).compute_for_slate(slate)
    score = JornadaScoreRepository(session).upsert_score(score)
    session.commit()
    return score


def _existing_training_run(session: Session) -> ModelTrainingRunModel:
    """Insert a minimal fake training run so rollback tests have something to look at."""
    run = ModelTrainingRunModel(
        model_name="elo_poisson_blend",
        training_sample_size=10,
        artifact_json=json.dumps({"model_type": "heuristic_blend", "training_sample_size": 10}),
    )
    session.add(run)
    session.commit()
    return run


# Low thresholds so tests don't need 50 matches to hit "ready"
_LOW_THRESHOLDS = RetrainingThresholds(
    min_trainable_rows=1,
    min_complete_slates=1,
    max_conflict_rate=0.05,
    max_blocked_rate_for_full_retrain=0.60,
    min_new_rows_since_last_train=1,
)


def _setup_complete_jornada(
    session: Session,
    draw_code: str = "PG-RT-COMP",
    n: int = 2,
) -> Any:
    """Create a slate, add predictions + results for all matches, score it."""
    src = _source(session, f"src-{draw_code}")
    slate = _make_slate(session, draw_code, n=n)
    match_ids = [sm.match_id for sm in slate.matches]
    for mid in match_ids:
        _add_result(session, mid, src.id, result_code="1")
        _add_prediction(session, mid, slate.id, slate.composition_hash, recommended_outcome="1")
    score = _score_slate(session, slate)
    assert score.is_complete
    return slate


# ---------------------------------------------------------------------------
# Tests: readiness gates
# ---------------------------------------------------------------------------

class TestReadinessGates:
    def test_not_ready_when_no_trainable_rows(self, db: Session):
        """No scored jornadas → readiness=False with skip recommendation."""
        svc = AdaptiveRetrainingService(db)
        report = svc.evaluate_readiness()
        assert report.ready is False
        assert report.recommended_action == "skip"
        assert report.trainable_rows == 0
        assert any(not c.passed for c in report.checks)

    def test_not_ready_below_min_trainable_rows(self, db: Session):
        """1 row but threshold=5 → not ready."""
        _setup_complete_jornada(db, "PG-RT-1R", n=1)
        thresholds = RetrainingThresholds(
            min_trainable_rows=5,
            min_complete_slates=1,
            max_conflict_rate=0.10,
            max_blocked_rate_for_full_retrain=0.60,
            min_new_rows_since_last_train=0,
        )
        report = AdaptiveRetrainingService(db).evaluate_readiness(thresholds)
        assert report.ready is False
        assert report.recommended_action == "skip"
        failed = [c for c in report.checks if not c.passed]
        assert any(c.name == "min_trainable_rows" for c in failed)

    def test_not_ready_below_min_complete_slates(self, db: Session):
        """1 complete slate but threshold=3 → not ready."""
        _setup_complete_jornada(db, "PG-RT-1S", n=2)
        thresholds = RetrainingThresholds(
            min_trainable_rows=1,
            min_complete_slates=3,
            max_conflict_rate=0.10,
            max_blocked_rate_for_full_retrain=0.60,
            min_new_rows_since_last_train=0,
        )
        report = AdaptiveRetrainingService(db).evaluate_readiness(thresholds)
        assert report.ready is False
        failed = [c for c in report.checks if not c.passed]
        assert any(c.name == "min_complete_slates" for c in failed)

    def test_not_ready_with_high_conflict_rate(self, db: Session):
        """Conflict rate exceeds max_conflict_rate → not ready."""
        # Set up a slate where one match has conflicting sources
        src_a = _source(db, "src-conf-a-rt", priority=10)
        src_b = _source(db, "src-conf-b-rt", priority=20)
        slate = _make_slate(db, "PG-RT-CONF", n=2)
        match_ids = [sm.match_id for sm in slate.matches]

        # match 0: canonical
        _add_result(db, match_ids[0], src_a.id, result_code="1")
        _add_prediction(db, match_ids[0], slate.id, slate.composition_hash, recommended_outcome="1")

        # match 1: conflict → excluded from trainable but counted in conflict_rate
        _add_result(db, match_ids[1], src_a.id, result_code="1")
        _add_result(db, match_ids[1], src_b.id, result_code="X")
        _add_prediction(db, match_ids[1], slate.id, slate.composition_hash, recommended_outcome="1")

        _score_slate(db, slate)

        thresholds = RetrainingThresholds(
            min_trainable_rows=1,
            min_complete_slates=1,
            max_conflict_rate=0.01,  # very tight
            max_blocked_rate_for_full_retrain=0.60,
            min_new_rows_since_last_train=0,
        )
        report = AdaptiveRetrainingService(db).evaluate_readiness(thresholds)
        assert report.ready is False
        failed = [c for c in report.checks if not c.passed]
        assert any(c.name == "max_conflict_rate" for c in failed)

    def test_ready_when_enough_data(self, db: Session):
        """Enough complete rows with low conflict rate → ready."""
        _setup_complete_jornada(db, "PG-RT-OK", n=3)
        report = AdaptiveRetrainingService(db).evaluate_readiness(_LOW_THRESHOLDS)
        assert report.ready is True
        assert report.recommended_action != "skip"


# ---------------------------------------------------------------------------
# Tests: dry_run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_modify_db(self, db: Session):
        """dry_run never creates a ModelTrainingRunModel."""
        _setup_complete_jornada(db, "PG-RT-DRY", n=2)
        before_runs = db.scalars(select(ModelTrainingRunModel)).all()
        AdaptiveRetrainingService(db).dry_run(_LOW_THRESHOLDS)
        after_runs = db.scalars(select(ModelTrainingRunModel)).all()
        assert len(after_runs) == len(before_runs)

    def test_dry_run_recommends_skip_when_no_data(self, db: Session):
        report = AdaptiveRetrainingService(db).dry_run()
        assert report.recommended_action == "skip"
        assert report.ready is False

    def test_dry_run_recommends_recalibrate_when_no_new_rows(self, db: Session):
        """When there are rows but new_rows < threshold → recalibrate_only."""
        _setup_complete_jornada(db, "PG-RT-RECAL", n=2)
        _existing_training_run(db)  # simulate a recent training run
        thresholds = RetrainingThresholds(
            min_trainable_rows=1,
            min_complete_slates=1,
            max_conflict_rate=0.10,
            max_blocked_rate_for_full_retrain=0.60,
            min_new_rows_since_last_train=100,  # very high — nothing is "new"
        )
        report = AdaptiveRetrainingService(db).dry_run(thresholds)
        assert report.ready is True
        assert report.recommended_action == "recalibrate_only"

    def test_dry_run_recommends_full_retrain_when_ready(self, db: Session):
        _setup_complete_jornada(db, "PG-RT-FULL", n=3)
        report = AdaptiveRetrainingService(db).dry_run(_LOW_THRESHOLDS)
        assert report.ready is True
        assert report.recommended_action == "full_xgboost_retrain"

    def test_dry_run_recommends_band_adjustment_when_blocked_rate_high(self, db: Session):
        """When blocked_rate > threshold → confidence_band_adjustment."""
        # Create jornada with all blocked predictions
        src = _source(db, "src-blocked-rt")
        slate = _make_slate(db, "PG-RT-BLOCK", n=2)
        match_ids = [sm.match_id for sm in slate.matches]
        for mid in match_ids:
            _add_result(db, mid, src.id, result_code="1")
            _add_prediction(db, mid, slate.id, slate.composition_hash,
                            recommended_outcome="1", confidence_band="blocked")
        _score_slate(db, slate)

        thresholds = RetrainingThresholds(
            min_trainable_rows=1,
            min_complete_slates=1,
            max_conflict_rate=0.10,
            max_blocked_rate_for_full_retrain=0.10,  # tight — blocked_rate will exceed this
            min_new_rows_since_last_train=0,
        )
        report = AdaptiveRetrainingService(db).dry_run(thresholds)
        assert report.ready is True
        assert report.recommended_action == "confidence_band_adjustment"


# ---------------------------------------------------------------------------
# Tests: run_retraining_if_ready
# ---------------------------------------------------------------------------

class TestRunRetraining:
    def test_run_blocks_when_not_ready(self, db: Session):
        """No data → success=False, ready=False."""
        result = AdaptiveRetrainingService(db).run_retraining_if_ready()
        assert result.success is False
        assert result.ready is False
        assert result.training_run_id is None
        assert result.reasons  # non-empty list of reasons

    def test_run_creates_training_run_when_ready(self, db: Session):
        """With enough canonical data, run_retraining_if_ready creates a new run."""
        _setup_complete_jornada(db, "PG-RT-EXEC", n=2)
        before_runs = db.scalars(select(ModelTrainingRunModel)).all()
        result = AdaptiveRetrainingService(db).run_retraining_if_ready(_LOW_THRESHOLDS)
        after_runs = db.scalars(select(ModelTrainingRunModel)).all()
        assert result.success is True
        assert result.ready is True
        assert result.training_run_id is not None
        assert len(after_runs) > len(before_runs)

    def test_run_does_not_delete_previous_training_run(self, db: Session):
        """Previous training runs must survive a retrain."""
        old_run = _existing_training_run(db)
        _setup_complete_jornada(db, "PG-RT-PREV", n=2)
        AdaptiveRetrainingService(db).run_retraining_if_ready(_LOW_THRESHOLDS)
        # The old run must still exist in the DB
        still_there = db.get(ModelTrainingRunModel, old_run.id)
        assert still_there is not None
        assert still_there.id == old_run.id

    def test_run_rollback_id_points_to_previous_run(self, db: Session):
        """rollback_run_id == ID of the training run that existed before."""
        old_run = _existing_training_run(db)
        _setup_complete_jornada(db, "PG-RT-RB", n=2)
        result = AdaptiveRetrainingService(db).run_retraining_if_ready(_LOW_THRESHOLDS)
        assert result.rollback_run_id == old_run.id

    def test_run_returns_comparison_when_rows_available(self, db: Session):
        """A successful retrain with scored rows returns a non-None comparison."""
        _setup_complete_jornada(db, "PG-RT-CMP", n=2)
        result = AdaptiveRetrainingService(db).run_retraining_if_ready(_LOW_THRESHOLDS)
        assert result.success is True
        assert result.comparison is not None
        assert result.comparison.rows_evaluated > 0


# ---------------------------------------------------------------------------
# Tests: data integrity guarantees
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_conflicting_results_not_in_trainable_rows(self, db: Session):
        """Matches with conflicting result_codes from 2 sources are excluded."""
        src_a = _source(db, "dint-src-a", priority=10)
        src_b = _source(db, "dint-src-b", priority=20)
        slate = _make_slate(db, "PG-RT-DINT", n=2)
        match_ids = [sm.match_id for sm in slate.matches]

        # match 0: canonical
        _add_result(db, match_ids[0], src_a.id, result_code="1")
        _add_prediction(db, match_ids[0], slate.id, slate.composition_hash, recommended_outcome="1")

        # match 1: conflict
        _add_result(db, match_ids[1], src_a.id, result_code="1")
        _add_result(db, match_ids[1], src_b.id, result_code="X")
        _add_prediction(db, match_ids[1], slate.id, slate.composition_hash, recommended_outcome="1")

        _score_slate(db, slate)

        from app.services.adaptive_dataset_service import AdaptiveDatasetService

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id, include_partial=True)
        assert all(r.match_id == match_ids[0] for r in rows)
        assert not any(r.match_id == match_ids[1] for r in rows)

    def test_null_slate_id_predictions_not_in_rows(self, db: Session):
        """Predictions with slate_id=None never appear in trainable rows."""
        src = _source(db, "null-sid-rt-src")
        slate = _make_slate(db, "PG-RT-NULLSID", n=1)
        match_id = slate.matches[0].match_id
        _add_result(db, match_id, src.id, result_code="1")
        # Prediction with NULL slate_id — should never produce a trainable row
        _add_prediction(db, match_id, slate_id=None, composition_hash=None)
        _score_slate(db, slate)

        from app.services.adaptive_dataset_service import AdaptiveDatasetService

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id, include_partial=True)
        assert rows == []

    def test_build_training_window_returns_metadata(self, db: Session):
        """build_training_window returns a dict with the expected keys."""
        _setup_complete_jornada(db, "PG-RT-WIN", n=2)
        window = AdaptiveRetrainingService(db).build_training_window()
        assert "complete_slates" in window
        assert "trainable_rows" in window
        assert "new_rows_since_last_train" in window
        assert "by_confidence_band" in window
        assert window["complete_slates"] >= 1
        assert window["trainable_rows"] >= 1
