"""Worker scheduled retrain — keeps the scoring artifact from going stale.

Pins the three contract points of `_maybe_retrain_model`:

  * with no prior training run, the job trains immediately (bootstrap);
  * a fresh run inside the interval is respected — no retrigger, so worker
    restarts never cause redundant expensive trains (the gate lives in the
    DB's `trained_at`, not in worker memory);
  * a stale run older than the interval triggers a new training run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _configure_db(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'retrain.db'}")
    run_migrations(db_session.engine)
    return db_session


def _run_count(session) -> int:
    from sqlalchemy import func, select

    from app.models.tables import ModelTrainingRunModel
    from app.services.model_training_service import ModelTrainingService

    return int(
        session.scalar(
            select(func.count()).select_from(ModelTrainingRunModel).where(
                ModelTrainingRunModel.model_name == ModelTrainingService.MODEL_NAME
            )
        )
        or 0
    )


def _backdate_latest_run(session, *, hours: int) -> None:
    from sqlalchemy import select

    from app.models.tables import ModelTrainingRunModel

    run = session.scalars(select(ModelTrainingRunModel)).first()
    run.trained_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    session.commit()


def test_retrain_bootstraps_when_no_run_exists(tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "model_retrain_interval_hours", 24)
    db_session = _configure_db(tmp_path)
    worker = SchedulerWorker()
    session = db_session.SessionLocal()
    try:
        worker._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 1
    finally:
        session.close()


def test_retrain_respects_interval_and_survives_restarts(tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "model_retrain_interval_hours", 24)
    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        SchedulerWorker()._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 1
        # A brand-new worker (fresh in-memory state, as after a restart) must
        # NOT retrigger: the gate is the run's trained_at in the DB.
        SchedulerWorker()._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 1
    finally:
        session.close()


def test_retrain_fires_when_latest_run_is_stale(tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "model_retrain_interval_hours", 24)
    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        SchedulerWorker()._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 1
        _backdate_latest_run(session, hours=25)
        SchedulerWorker()._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 2
    finally:
        session.close()


def test_retrain_disabled_with_zero_interval(tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "model_retrain_interval_hours", 0)
    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        SchedulerWorker()._maybe_retrain_model(session, datetime.now(timezone.utc))
        assert _run_count(session) == 0
    finally:
        session.close()
