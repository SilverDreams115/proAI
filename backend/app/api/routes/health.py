from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from fastapi import APIRouter
from fastapi import Request
from fastapi import Response
from fastapi.responses import PlainTextResponse

from app.core.metrics import metrics_store
from app.core.settings import settings
from app.db.health import get_database_health
from app.schemas.health import HealthResponse
from app.schemas.health import ReadyResponse

router = APIRouter(tags=["health"])
START_TIME = monotonic()


def _collect_operational_signals() -> dict[str, object]:
    """Pull the operational-health signals layered onto /health (P8).
    Each lookup is wrapped so a transient failure on one signal doesn't
    blank out the rest of the response.
    """
    from sqlalchemy import select

    from app.db import session as db_session
    from app.models.tables import IngestionRunModel, SourceModel
    from app.parsers.registry import parser_registry
    from app.workers.scheduler_worker import worker as worker_module

    signals: dict[str, object] = {
        "last_ingest_at": None,
        "last_ingest_age_seconds": None,
        "last_ingest_status": None,
        "backtest_verdict_generated_at": None,
        "backtest_verdict_age_seconds": None,
        "worker_last_executed_at": None,
        "worker_last_polled_at": None,
        "unregistered_parser_sources": 0,
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
                try:
                    parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                    signals["backtest_verdict_age_seconds"] = round((now - parsed).total_seconds(), 1)
                except ValueError:
                    pass
    except Exception:
        pass

    try:
        state = worker_module._state
        # WorkerState stores these as ISO-formatted strings (set via .isoformat()
        # in the worker loop); assign directly, no second .isoformat() call needed.
        if state.last_executed_at is not None:
            signals["worker_last_executed_at"] = state.last_executed_at
        if state.last_polled_at is not None:
            signals["worker_last_polled_at"] = state.last_polled_at
    except Exception:
        pass

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
async def openapi_schema(request: "Request") -> dict[str, object]:  # type: ignore[name-defined]
    """Return the OpenAPI 3 schema regardless of `settings.docs_enabled`.

    Production deployments turn off the public `/openapi.json` and the
    Swagger UI so the API surface isn't exposed unauthenticated. This
    endpoint reuses the same generator but stays behind the API key /
    session auth, so an operator can still introspect available routes
    without breaking the production hardening.
    """
    return request.app.openapi()
