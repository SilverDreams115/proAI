"""Tests for the generic `ensure_refresh_job` (Fase 6.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.migrations import run_migrations
from app.models.tables import SourceModel
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.scheduler_repository import SchedulerRepository
from app.services.scheduler_service import SchedulerService


def _bootstrap(tmp_path) -> tuple[SchedulerService, sessionmaker, str]:
    engine = create_engine(f"sqlite:///{tmp_path / 'sched.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    source = SourceModel(
        name="Source A",
        base_url="https://example.test/feed",
        kind="json_feed",
        parser_profile="generic",
        is_active=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    return service, SessionLocal, source.id


def test_ensure_refresh_job_creates_new_job(tmp_path) -> None:
    """First call inserts a fresh ScheduledIngestionJobModel row."""
    service, SessionLocal, source_id = _bootstrap(tmp_path)
    job = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
    )
    assert job.job_name == "weekly-refresh"
    assert job.interval_minutes == 10080
    assert job.is_active is True


def test_ensure_refresh_job_is_idempotent_by_name(tmp_path) -> None:
    """Calling the helper twice with the same name must update — not
    duplicate — the existing row."""
    service, SessionLocal, source_id = _bootstrap(tmp_path)
    first = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
    )
    second = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=60,
    )
    assert first.id == second.id
    assert second.interval_minutes == 60  # update reflected


def test_ensure_refresh_job_preserves_pending_next_run(tmp_path) -> None:
    """When the job already has a scheduled `next_run_at`, the helper
    must not overwrite it — operators rely on that timestamp to know
    when the next ingestion will fire."""
    service, SessionLocal, source_id = _bootstrap(tmp_path)
    locked_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    first = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
        next_run_at=locked_at,
    )
    # SQLite drops tzinfo on read; compare the naive UTC components instead.
    first_naive = first.next_run_at.replace(tzinfo=None) if first.next_run_at else None
    assert first_naive == locked_at.replace(tzinfo=None)
    later = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
        next_run_at=locked_at + timedelta(days=7),
    )
    later_naive = later.next_run_at.replace(tzinfo=None) if later.next_run_at else None
    # The helper only sets `next_run_at` when it was None; an existing
    # schedule keeps the original.
    assert later_naive == locked_at.replace(tzinfo=None)


def test_ensure_refresh_job_can_reactivate_a_disabled_job(tmp_path) -> None:
    """If an old job was paused (is_active=False), calling the helper
    must re-enable it so the worker picks it up again."""
    service, SessionLocal, source_id = _bootstrap(tmp_path)
    job = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
        is_active=False,
    )
    assert job.is_active is False
    job = service.ensure_refresh_job(
        source_id=source_id,
        job_name="weekly-refresh",
        interval_minutes=10080,
        is_active=True,
    )
    assert job.is_active is True


def test_current_progol_refresh_uses_shared_helper(tmp_path) -> None:
    """The historical `ensure_current_progol_refresh_job` is now a thin
    wrapper over `ensure_refresh_job`. The contract must remain stable
    so existing worker code keeps functioning."""
    service, SessionLocal, source_id = _bootstrap(tmp_path)
    job = service.ensure_current_progol_refresh_job(
        source_id=source_id,
        interval_minutes=60,
    )
    assert job.job_name == "current-progol-refresh"
    # Re-running keeps the same row.
    again = service.ensure_current_progol_refresh_job(
        source_id=source_id,
        interval_minutes=30,
    )
    assert again.id == job.id
    assert again.interval_minutes == 30
