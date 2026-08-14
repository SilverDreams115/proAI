from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from fastapi.responses import PlainTextResponse

from app.api.deps import require_worker_auth
from app.core.metrics import metrics_store
from app.core.settings import settings
from app.db.health import get_database_health
from app.schemas.health import HealthResponse
from app.schemas.health import ReadyResponse

router = APIRouter(tags=["health"])
START_TIME = monotonic()


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_backup_marker() -> datetime | None:
    """Timestamp of the last successful pg_dump, or None if unknowable.

    The backup job writes an epoch into a marker file on the shared volume
    after each dump. Returning None covers every "we cannot tell" case —
    volume not mounted, fresh install, unreadable or malformed file — and
    callers must treat None as *no signal*, never as a stale backup. A test
    run or a dev box without the volume would otherwise report degraded
    forever.

    Never raises: /health must not fail because of an optional probe.
    """
    marker = settings.health_backup_marker_path
    if not marker:
        return None
    try:
        raw = Path(marker).read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def _read_local_context_state() -> tuple[bool | None, str | None]:
    """Is the Progol context the refresh job reads actually there?

    `./data/progol_context` is a bind mount, and under Docker Desktop on WSL
    the daemon stages bind mounts behind /run/desktop/mnt/host/wsl/... — when
    that staging stops resolving the directory mounts EMPTY instead of
    failing, so the host file is present, the container sees nothing, and
    `current-progol-refresh` dies on FileNotFoundError every retry. That is
    invisible here: the failure happens before an ingestion_run row exists,
    so `last_ingest_*` keeps reporting the last SUCCESS and health stays ok.
    It went unnoticed for hours on 2026-08-14, and the same mount mechanism
    had already killed the backup sidecar for four days.

    Resolved through the service so this can never drift from the path the
    job actually opens.

    Tri-state, following the same rule the backup marker follows: None means
    "cannot tell", never "broken". The discriminator is the PARENT directory.
    A lost mount leaves the mount point in place and empty, so directory
    present + file absent is a real fault. Directory absent means this
    deployment has no local context wired up at all — a fresh install or a
    test process — and reporting that as a failure would degrade every
    environment that simply does not use one.
    """
    try:
        from app.services.current_progol_service import CurrentProgolService

        path = CurrentProgolService._resolve_context_path(None)
        if path.is_file():
            return True, str(path)
        if path.parent.is_dir():
            return False, str(path)
        return None, str(path)
    except Exception:  # pragma: no cover - non-fatal observation
        return None, None


def _freshness_alert(
    *,
    signal: str,
    age_seconds: object,
    warning_threshold: float,
    critical_threshold: float,
) -> dict[str, object] | None:
    if not isinstance(age_seconds, (int, float)):
        return None
    severity: str | None = None
    threshold = warning_threshold
    if age_seconds >= critical_threshold:
        severity = "critical"
        threshold = critical_threshold
    elif age_seconds >= warning_threshold:
        severity = "warning"
    if severity is None:
        return None
    return {
        "signal": signal,
        "severity": severity,
        "age_seconds": round(float(age_seconds), 1),
        "threshold_seconds": float(threshold),
        "message": f"{signal} age {round(float(age_seconds), 1)}s exceeds {severity} threshold {threshold}s",
    }


def _worker_status(worker_last_executed_at: object, worker_last_polled_age_seconds: object) -> str:
    if not isinstance(worker_last_polled_age_seconds, (int, float)):
        return "unknown"
    if worker_last_polled_age_seconds >= settings.health_worker_poll_critical_age_seconds:
        return "stale"
    if worker_last_polled_age_seconds >= settings.health_worker_poll_warning_age_seconds:
        return "degraded"
    if worker_last_executed_at is not None:
        return "executed"
    return "polling"


def _collect_operational_signals() -> dict[str, object]:
    """Pull the operational-health signals layered onto /health (P8).
    Each lookup is wrapped so a transient failure on one signal doesn't
    blank out the rest of the response.
    """
    from sqlalchemy import select

    from app.db import session as db_session
    from app.models.tables import IngestionRunModel, SourceModel
    from app.parsers.registry import parser_registry
    from app.workers.scheduler_worker import read_worker_heartbeat
    from app.workers.scheduler_worker import worker as worker_module

    signals: dict[str, object] = {
        "last_ingest_at": None,
        "last_ingest_age_seconds": None,
        "last_ingest_status": None,
        "backtest_verdict_generated_at": None,
        "backtest_verdict_age_seconds": None,
        "worker_last_executed_at": None,
        "worker_last_polled_at": None,
        "worker_last_polled_age_seconds": None,
        "worker_status": "unknown",
        "backup_last_success_at": None,
        "backup_age_seconds": None,
        "freshness_alerts": [],
        "unregistered_parser_sources": 0,
        "local_context_readable": None,
        "local_context_path": None,
    }
    now = datetime.now(timezone.utc)

    # Last successful ingest. We pick the most recent run regardless of
    # status because operators want to see _both_ the last attempt and
    # whether it succeeded — surfacing only successes hides a stalled
    # source that's been failing for days.
    try:
        s = db_session.SessionLocal()
        try:
            row = s.scalar(
                select(IngestionRunModel).order_by(IngestionRunModel.started_at.desc()).limit(1)
            )
            if row is not None:
                signals["last_ingest_at"] = row.started_at.isoformat()
                signals["last_ingest_status"] = row.status
                signals["last_ingest_age_seconds"] = round((now - row.started_at).total_seconds(), 1)
            # Misconfigured sources (parser_profile missing from registry)
            # silently drop fixtures payloads — surface the count so the
            # operator notices before the next scheduled refresh fires.
            active = s.scalars(select(SourceModel).where(SourceModel.is_active.is_(True))).all()
            signals["unregistered_parser_sources"] = sum(
                1 for src in active if not parser_registry.has(src.parser_profile)
            )
        finally:
            s.close()
    except Exception:  # pragma: no cover - non-fatal observation
        pass

    try:
        index_path = Path("/data/backtest_history/index.json")
        if not index_path.is_file():
            index_path = Path("reports/backtest_history/index.json")
        if index_path.is_file():
            import json as _json

            data = _json.loads(index_path.read_text(encoding="utf-8"))
            generated_at = data.get("generated_at")
            if isinstance(generated_at, str):
                signals["backtest_verdict_generated_at"] = generated_at
                parsed = _parse_iso_datetime(generated_at)
                if parsed is not None:
                    signals["backtest_verdict_age_seconds"] = round((now - parsed).total_seconds(), 1)
    except Exception:
        pass

    try:
        heartbeat = read_worker_heartbeat()
        if heartbeat:
            signals["worker_last_executed_at"] = heartbeat.get("last_executed_at")
            signals["worker_last_polled_at"] = heartbeat.get("last_polled_at")
        else:
            state = worker_module._state
            # WorkerState stores these as ISO-formatted strings (set via .isoformat()
            # in the worker loop); assign directly, no second .isoformat() call needed.
            if state.last_executed_at is not None:
                signals["worker_last_executed_at"] = state.last_executed_at
            if state.last_polled_at is not None:
                signals["worker_last_polled_at"] = state.last_polled_at
    except Exception:
        pass

    worker_polled_at = _parse_iso_datetime(signals.get("worker_last_polled_at"))
    if worker_polled_at is not None:
        signals["worker_last_polled_age_seconds"] = round((now - worker_polled_at).total_seconds(), 1)
    signals["worker_status"] = _worker_status(
        signals.get("worker_last_executed_at"),
        signals.get("worker_last_polled_age_seconds"),
    )

    backup_at = _read_backup_marker()
    if backup_at is not None:
        signals["backup_last_success_at"] = backup_at.isoformat()
        signals["backup_age_seconds"] = round((now - backup_at).total_seconds(), 1)

    alerts = []
    for alert in (
        _freshness_alert(
            signal="last_ingest",
            age_seconds=signals.get("last_ingest_age_seconds"),
            warning_threshold=settings.health_last_ingest_warning_age_seconds,
            critical_threshold=settings.health_last_ingest_critical_age_seconds,
        ),
        _freshness_alert(
            signal="backtest_verdict",
            age_seconds=signals.get("backtest_verdict_age_seconds"),
            warning_threshold=settings.health_backtest_warning_age_seconds,
            critical_threshold=settings.health_backtest_critical_age_seconds,
        ),
        _freshness_alert(
            signal="worker_poll",
            age_seconds=signals.get("worker_last_polled_age_seconds"),
            warning_threshold=settings.health_worker_poll_warning_age_seconds,
            critical_threshold=settings.health_worker_poll_critical_age_seconds,
        ),
        # Only alerts when the marker was actually read; a missing marker
        # leaves backup_age_seconds as None and _freshness_alert returns None
        # for anything that is not a number.
        _freshness_alert(
            signal="backup",
            age_seconds=signals.get("backup_age_seconds"),
            warning_threshold=settings.health_backup_warning_age_seconds,
            critical_threshold=settings.health_backup_critical_age_seconds,
        ),
    ):
        if alert is not None:
            alerts.append(alert)

    context_readable, context_path = _read_local_context_state()
    signals["local_context_readable"] = context_readable
    signals["local_context_path"] = context_path
    if context_readable is False:
        # Not a freshness alert — there is no age to compare, the file is
        # simply not there. It rides the same list so anything already
        # watching freshness_alerts sees it without changing shape.
        alerts.append(
            {
                "signal": "local_context",
                "severity": "critical",
                "age_seconds": 0.0,
                "threshold_seconds": 0.0,
                "message": (
                    f"local Progol context unreadable at {context_path or 'unresolved path'}; "
                    "current-progol-refresh cannot run (check the data/progol_context bind mount)"
                ),
            }
        )

    signals["freshness_alerts"] = alerts

    return signals


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_health = get_database_health()
    ops = _collect_operational_signals()
    # Status starts as "ok" when the schema matches, degrades when an
    # operational signal is missing/stale enough to warrant attention.
    status = "ok" if db_health["schema_up_to_date"] else "degraded"
    _unreg = ops.get("unregistered_parser_sources")
    if isinstance(_unreg, int) and _unreg > 0:
        status = "degraded"
    if ops.get("freshness_alerts"):
        status = "degraded"
    return HealthResponse(
        status=status,
        service="proAI-backend",
        version=settings.app_version,
        environment=settings.environment,
        uptime_seconds=round(monotonic() - START_TIME, 3),
        database_ok=bool(db_health["database_ok"]),
        schema_version=int(db_health["schema_version"]),
        schema_up_to_date=bool(db_health["schema_up_to_date"]),
        **ops,  # type: ignore[arg-type]
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready(response: Response) -> ReadyResponse:
    db_health = get_database_health()
    is_ready = bool(db_health["database_ok"]) and bool(db_health["schema_up_to_date"])
    if not is_ready:
        response.status_code = 503
    return ReadyResponse(
        status="ready" if is_ready else "not_ready",
        ready=is_ready,
        database_ok=bool(db_health["database_ok"]),
        schema_up_to_date=bool(db_health["schema_up_to_date"]),
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        metrics_store.render_prometheus(
            app_name=settings.app_name,
            app_version=settings.app_version,
            environment=settings.environment,
        )
    )


@router.get("/openapi-schema")
async def openapi_schema(
    request: Request,
    _: None = Depends(require_worker_auth),
) -> dict[str, object]:
    """Return the OpenAPI 3 schema for authenticated operators.

    Production deployments turn off the public `/openapi.json` and the
    Swagger UI so the API surface isn't exposed unauthenticated. This
    endpoint reuses the same generator but stays behind the API key /
    session auth (enforced by require_worker_auth), so an operator can
    still introspect available routes without breaking the production
    hardening.

    In bare-dev (no credentials configured) the guard is a no-op and the
    endpoint is open, consistent with the all-routes-open dev posture.
    """
    return request.app.openapi()
