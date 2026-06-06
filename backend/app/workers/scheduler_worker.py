from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
import logging

from app.core.logging import configure_logging
from app.core.settings import settings
from app.db import session as db_session
from app.db.migrations import run_migrations
from app.models import tables  # noqa: F401
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.scheduler_repository import SchedulerRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.services.current_progol_service import CurrentProgolService
from app.services.scheduler_service import SchedulerService
from app.services.slate_proposal_service import SlateProposalService
from app.services.slate_service import SlateService

configure_logging(level=settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger("proai.worker.scheduler")


@dataclass(slots=True)
class WorkerRunSummary:
    iterations: int
    executed_runs: int
    failed_iterations: int = 0


@dataclass(slots=True)
class WorkerState:
    executed_runs: int = 0
    failed_iterations: int = 0
    last_polled_at: str | None = None
    last_executed_at: str | None = None
    last_error_at: str | None = None
    last_error_message: str | None = None
    last_cycle_duration_ms: float | None = None
    last_proposal_observed_at: datetime | None = None
    last_proposal_status: str | None = None
    last_proposal_draw_code: str | None = None
    last_auto_promoted_at: datetime | None = None
    last_auto_promoted_draw_code: str | None = None
    # In-memory marker for the periodic source_documents prune. The
    # state resets on worker restart, which is fine — the prune is
    # idempotent and the wakeup interval (24h) is cheap to re-run.
    last_maintenance_at: datetime | None = None
    last_maintenance_deleted: int = 0


class SchedulerWorker:
    def __init__(self) -> None:
        self._state = WorkerState()

    def run_once(self) -> int:
        started = perf_counter()
        session = None
        try:
            run_migrations(db_session.engine)
            session = db_session.SessionLocal()
            polled_at = datetime.now(timezone.utc)
            self._state.last_polled_at = polled_at.isoformat()
            ingestion_repository = IngestionRepository(session)
            service = SchedulerService(SchedulerRepository(session), ingestion_repository)
            if settings.current_progol_auto_refresh_enabled:
                source = CurrentProgolService(
                    SourceRepository(session),
                    ingestion_repository,
                    SlateRepository(session),
                ).ensure_default_context_source()
                service.ensure_current_progol_refresh_job(
                    source_id=source.id,
                    interval_minutes=settings.current_progol_refresh_interval_minutes,
                    job_name=settings.current_progol_refresh_job_name,
                )
            archived_codes = SlateService(SlateRepository(session)).archive_due_slates(polled_at)
            if archived_codes:
                logger.info(
                    "slates auto-archived after cierre",
                    extra={
                        "event": "slates_auto_archived",
                        "archived_codes": archived_codes,
                        "count": len(archived_codes),
                    },
                )
            self._maybe_observe_proposal(session, polled_at)
            self._maybe_auto_promote_proposals(session, polled_at)
            self._maybe_run_maintenance(session, polled_at)
            runs = service.run_due_jobs()
            self._state.executed_runs += len(runs)
            if runs:
                self._state.last_executed_at = datetime.now(timezone.utc).isoformat()
            self._state.last_error_message = None
            self._state.last_cycle_duration_ms = round((perf_counter() - started) * 1000, 2)
            logger.info(
                "scheduler worker cycle completed",
                extra={
                    "event": "scheduler_worker_cycle_completed",
                    "executed_runs": len(runs),
                    "total_executed_runs": self._state.executed_runs,
                    "duration_ms": self._state.last_cycle_duration_ms,
                    "polled_at": self._state.last_polled_at,
                },
            )
            return len(runs)
        except Exception as exc:
            self._state.failed_iterations += 1
            self._state.last_error_at = datetime.now(timezone.utc).isoformat()
            self._state.last_error_message = str(exc)
            self._state.last_cycle_duration_ms = round((perf_counter() - started) * 1000, 2)
            logger.exception(
                "scheduler worker cycle failed",
                extra={
                    "event": "scheduler_worker_cycle_failed",
                    "failed_iterations": self._state.failed_iterations,
                    "duration_ms": self._state.last_cycle_duration_ms,
                },
            )
            raise
        finally:
            if session is not None:
                session.close()

    def _maybe_observe_proposal(self, session, polled_at: datetime) -> None:
        # Gate the LN PDF fetch to the configured interval (default 60min).
        # The worker cycles every 30s, so calling observe() on every cycle
        # would hammer LN. Tracking the last successful attempt on the
        # worker process state is sufficient — duplicate observations are
        # idempotent at the service layer, but we still want to avoid
        # unnecessary network traffic.
        if not settings.progol_proposal_observe_enabled:
            return
        interval = timedelta(minutes=max(1, settings.progol_proposal_observe_interval_minutes))
        last = self._state.last_proposal_observed_at
        if last is not None and (polled_at - last) < interval:
            return
        self._state.last_proposal_observed_at = polled_at
        try:
            proposal = SlateProposalService(session).observe()
        except Exception:
            logger.exception(
                "progol proposal observation failed",
                extra={"event": "progol_proposal_observe_failed"},
            )
            return
        if proposal is None:
            logger.info(
                "progol proposal observation produced no row",
                extra={"event": "progol_proposal_observe_noop"},
            )
            return
        self._state.last_proposal_status = proposal.status
        self._state.last_proposal_draw_code = proposal.draw_code
        logger.info(
            "progol proposal observation recorded",
            extra={
                "event": "progol_proposal_observed",
                "draw_code": proposal.draw_code,
                "status": proposal.status,
                "observations": proposal.observations,
            },
        )

    def _maybe_auto_promote_proposals(self, session, polled_at: datetime) -> None:
        # Fase 3: turn a validated proposal into a real slate without an
        # operator click, but ONLY when we're close to the active
        # slate's cierre (or there's nothing active right now). This
        # guards against premature promotion when LN republishes a guide
        # several days in advance — the dual-time validation already
        # confirmed the fixtures so the only remaining risk is timing.
        if not settings.progol_auto_promote_enabled:
            return
        threshold = timedelta(hours=max(0.0, float(settings.progol_auto_promote_threshold_hours)))

        active = SlateService(SlateRepository(session)).get_active_slate(polled_at)
        if active is not None:
            cierre = active.registration_closes_at
            if cierre is None:
                # No cierre on the active slate — refuse to promote
                # without that signal, an operator can still click.
                return
            if cierre.tzinfo is None:
                cierre = cierre.replace(tzinfo=timezone.utc)
            if (cierre - polled_at) > threshold:
                return

        proposal_service = SlateProposalService(session)
        validated = [
            p for p in proposal_service.list_proposals(status="validated") if not p.promoted_slate_id
        ]
        if not validated:
            return
        # Sort so the earliest cierre is promoted first — when LN stages
        # midweek + weekend simultaneously we want the chronologically
        # next one to land first.
        def _sort_key(p):
            cierre_at = p.registration_closes_at
            if cierre_at is None:
                return datetime.max.replace(tzinfo=timezone.utc)
            if cierre_at.tzinfo is None:
                return cierre_at.replace(tzinfo=timezone.utc)
            return cierre_at
        validated.sort(key=_sort_key)
        target = validated[0]
        try:
            slate = proposal_service.promote_proposal(target, actor="worker")
            session.commit()
        except ValueError as exc:
            logger.warning(
                "auto-promote skipped",
                extra={
                    "event": "progol_auto_promote_skipped",
                    "draw_code": target.draw_code,
                    "reason": str(exc),
                },
            )
            return
        except Exception:
            logger.exception(
                "auto-promote failed",
                extra={
                    "event": "progol_auto_promote_failed",
                    "draw_code": target.draw_code,
                },
            )
            session.rollback()
            return
        self._state.last_auto_promoted_at = polled_at
        self._state.last_auto_promoted_draw_code = target.draw_code
        logger.info(
            "progol proposal auto-promoted",
            extra={
                "event": "progol_proposal_auto_promoted",
                "draw_code": target.draw_code,
                "slate_id": slate.id,
                "active_slate_present": active is not None,
            },
        )

    def _maybe_run_maintenance(self, session, polled_at: datetime) -> None:
        """Periodically prune orphan source_documents from the worker
        so operators don't have to remember to crontab the CLI. The
        prune is cheap (a single DELETE with three predicates and
        all-indexed columns) but we still gate it behind the
        configured interval so a hot-restart-loop doesn't hammer
        the table."""
        from sqlalchemy import and_, delete

        from app.models.tables import SourceDocumentModel

        interval_hours = settings.source_documents_prune_interval_hours
        retention_days = settings.source_documents_retention_days
        if interval_hours <= 0 or retention_days <= 0:
            return
        last = self._state.last_maintenance_at
        if last is not None and polled_at - last < timedelta(hours=interval_hours):
            return
        cutoff = polled_at - timedelta(days=retention_days)
        try:
            stmt = delete(SourceDocumentModel).where(
                and_(
                    SourceDocumentModel.matched_match_id.is_(None),
                    SourceDocumentModel.linked_evidence_id.is_(None),
                    SourceDocumentModel.captured_at < cutoff,
                )
            )
            result = session.execute(stmt)
            session.commit()
            deleted = int(result.rowcount or 0)
        except Exception:
            session.rollback()
            logger.exception(
                "source_documents prune failed",
                extra={"event": "source_documents_prune_failed", "cutoff": cutoff.isoformat()},
            )
            return
        self._state.last_maintenance_at = polled_at
        self._state.last_maintenance_deleted = deleted
        if deleted:
            logger.info(
                "source_documents pruned",
                extra={
                    "event": "source_documents_pruned",
                    "deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": retention_days,
                },
            )

    def run_loop(self, poll_interval_seconds: int = 30, max_iterations: int | None = None) -> WorkerRunSummary:
        executed_runs = 0
        failed_iterations = 0
        iterations = 0
        logger.info(
            "scheduler worker loop started",
            extra={
                "event": "scheduler_worker_loop_started",
                "poll_interval_seconds": poll_interval_seconds,
                "max_iterations": max_iterations,
            },
        )
        while max_iterations is None or iterations < max_iterations:
            try:
                executed_runs += self.run_once()
            except Exception:
                failed_iterations += 1
            iterations += 1
            time.sleep(poll_interval_seconds)
        logger.info(
            "scheduler worker loop stopped",
            extra={
                "event": "scheduler_worker_loop_stopped",
                "iterations": iterations,
                "executed_runs": executed_runs,
                "failed_iterations": failed_iterations,
            },
        )
        return WorkerRunSummary(
            iterations=iterations,
            executed_runs=executed_runs,
            failed_iterations=failed_iterations,
        )


def run_worker() -> None:
    """Module entrypoint for `python -m app.workers.scheduler_worker`.

    Named distinctly from `app.cli.main` so grep/jump-to-symbol in
    an editor lands on exactly one definition rather than asking
    which `main` you meant."""
    SchedulerWorker().run_loop(poll_interval_seconds=settings.worker_poll_interval_seconds)


worker = SchedulerWorker()


def get_worker_state() -> WorkerState:
    return worker._state


if __name__ == "__main__":
    run_worker()
