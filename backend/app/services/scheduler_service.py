from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from typing import Callable
from time import perf_counter

from app.core.errors import NotFoundError
from app.core.metrics import metrics_store
from app.core.settings import settings
from app.db.session import managed_transaction
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.scheduler_repository import SchedulerRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.schemas.scheduler import ScheduledJobCreate
from app.services.current_progol_service import CurrentProgolService
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


class SchedulerService:
    CURRENT_PROGOL_REFRESH_JOB_NAME = "current-progol-refresh"

    def __init__(
        self,
        repository: SchedulerRepository,
        ingestion_repository: IngestionRepository,
    ) -> None:
        self.repository = repository
        self.ingestion_repository = ingestion_repository

    def create_job(self, payload: ScheduledJobCreate):
        with managed_transaction(self.repository.session):
            return self.repository.create_job(payload)

    def ensure_current_progol_refresh_job(
        self,
        *,
        source_id: str,
        interval_minutes: int,
        job_name: str | None = None,
        next_run_at: datetime | None = None,
        is_active: bool = True,
    ):
        selected_job_name = job_name or self.CURRENT_PROGOL_REFRESH_JOB_NAME
        return self.ensure_refresh_job(
            source_id=source_id,
            job_name=selected_job_name,
            interval_minutes=interval_minutes,
            next_run_at=next_run_at,
            is_active=is_active,
        )

    def ensure_refresh_job(
        self,
        *,
        source_id: str,
        job_name: str,
        interval_minutes: int,
        next_run_at: datetime | None = None,
        is_active: bool = True,
    ):
        """Generic upsert for a scheduled ingestion job.

        Used by F6.4 to register a refresh cadence for any registered
        source (historical CSV, JSON feed, HTML page). Keeps the
        existing job_name semantics so re-running bootstrap is
        idempotent."""
        scheduled_at = next_run_at or datetime.now(timezone.utc)
        with managed_transaction(self.repository.session):
            existing = self.repository.get_job_by_name(job_name)
            if existing is None:
                return self.repository.create_job(
                    ScheduledJobCreate(
                        source_id=source_id,
                        job_name=job_name,
                        interval_minutes=interval_minutes,
                        next_run_at=scheduled_at,
                        is_active=is_active,
                    )
                )
            existing.source_id = source_id
            existing.interval_minutes = interval_minutes
            existing.is_active = is_active
            if existing.next_run_at is None:
                existing.next_run_at = scheduled_at
            return self.repository.save_job(existing)

    def list_jobs(self):
        return self.repository.list_jobs()

    def run_due_jobs(self, on_job_processed: Callable[[], None] | None = None):
        """Run all due scheduled jobs.

        ``on_job_processed`` is invoked after each job is processed and saved
        so a long-running batch (many overdue source refreshes) can refresh the
        worker liveness heartbeat between jobs instead of only at batch end.
        """
        now = datetime.now(timezone.utc)
        jobs = self.repository.list_due_jobs(now)
        runs = []
        for job in jobs:
            lease_until = now + timedelta(minutes=max(job.interval_minutes, 1))
            with managed_transaction(self.repository.session):
                claimed = self.repository.claim_job(job.id, job.next_run_at, lease_until)
            if not claimed:
                continue
            try:
                run = self._run_job(job)
            except Exception as exc:
                logger.exception(
                    "scheduled job execution failed",
                    extra={
                        "event": "scheduled_job_failed",
                        "job_name": job.job_name,
                        "source_id": getattr(job, "source_id", None),
                        "error_type": type(exc).__name__,
                    },
                )
                run = SimpleNamespace(status="failed", error_message=f"{type(exc).__name__}: {exc}")
            job.last_run_at = now
            if run.status == "completed":
                job.next_run_at = now + timedelta(minutes=job.interval_minutes)
            else:
                job.next_run_at = now + timedelta(minutes=min(job.interval_minutes, 5))
            with managed_transaction(self.repository.session):
                self.repository.save_job(job)
            runs.append(run)
            if on_job_processed is not None:
                on_job_processed()
        return runs

    def _run_job(self, job):
        if job.job_name in {self.CURRENT_PROGOL_REFRESH_JOB_NAME, settings.current_progol_refresh_job_name}:
            response = CurrentProgolService(
                SourceRepository(self.repository.session),
                self.ingestion_repository,
                SlateRepository(self.repository.session),
            ).refresh_current()
            return SimpleNamespace(status=response.ingestion_status, response=response)
        return IngestionService(self.ingestion_repository).run_for_source(job.source_id)

    def record_source_health(self, source_id: str):
        source = self.ingestion_repository.get_source(source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        started = perf_counter()
        status = "inactive"
        detail = "Source is inactive."
        if source.is_active:
            ingestion_service = IngestionService(self.ingestion_repository)
            try:
                connector = ingestion_service.get_connector_for_source(source)
                documents = ingestion_service._fetch_documents(connector)
                status = "healthy"
                detail = (
                    f"Connector fetch succeeded with {len(documents)} document(s)."
                    if documents
                    else "Connector fetch succeeded with no documents."
                )
            except Exception as exc:
                logger.warning(
                    "source health check failed",
                    extra={
                        "event": "source_health_failed",
                        "source_id": source_id,
                        "source_name": source.name,
                        "error_type": type(exc).__name__,
                    },
                )
                status = "degraded"
                detail = f"{type(exc).__name__}: {exc}"
        latency_ms = int((perf_counter() - started) * 1000)
        metrics_store.record_source_health_check(source_name=source.name, status=status)
        with managed_transaction(self.repository.session):
            return self.repository.add_health_check(source_id, status, latency_ms, detail)

    def list_health_checks(self):
        return self.repository.list_health_checks()
