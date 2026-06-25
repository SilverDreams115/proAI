from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import logging
import secrets

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.exc import IntegrityError

from app.api.routes import availability, evidence, history, ingestion, normalization, predictions, results, scheduler, slates, sources, stats, training, worker
from app.api.routes import scoring
from app.api.routes import live_results
from app.api.routes import adaptive_dataset
from app.api.routes import training_adaptive
from app.api.routes import training_neural
from app.api.routes import auth
from app.api.routes import operations
from app.api.routes import tracking
from app.api.routes import learning
from app.api.routes.health import router as health_router
from app.core.auth import verify_session_token
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.metrics import metrics_store
from app.core.observability import init_sentry
from app.core.settings import settings
from app.db import session as db_session
from app.db.migrations import run_migrations
from app.models import tables  # noqa: F401

configure_logging(level=settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger("proai.app")
PUBLIC_API_PATHS = {"/api/health", "/api/ready", "/api/auth/login", "/api/auth/logout", "/api/auth/session"}


def _backfill_composition_hashes() -> None:
    """One-time idempotent backfill for slates that predate composition_hash tracking.

    Runs at startup after migrations. Slates that already have a hash are
    skipped; no snapshots are invalidated. Non-fatal: a failure logs a warning
    and the app continues — backfill will retry on the next restart.
    """
    try:
        from app.repositories.slate_repository import SlateRepository

        session = db_session.SessionLocal()
        try:
            count = SlateRepository(session).backfill_composition_hashes()
            session.commit()
            if count:
                logger.info(
                    "startup composition_hash backfill complete",
                    extra={"event": "startup_composition_hash_backfill", "slates_updated": count},
                )
        finally:
            session.close()
    except Exception as exc:
        logger.warning("composition_hash backfill failed (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations(db_session.engine)
    _backfill_composition_hashes()
    _warn_about_misconfigured_sources()
    sentry_active = init_sentry(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
    )
    logger.info(
        "application started",
        extra={
            "event": "application_started",
            "environment": settings.environment,
            "database_url": settings.safe_database_url,
            "docs_enabled": settings.docs_enabled,
            "sentry_active": sentry_active,
        },
    )
    yield
    logger.info("application stopped", extra={"event": "application_stopped"})


def _warn_about_misconfigured_sources() -> None:
    """Catch source rows whose parser_profile no longer maps to a registered
    parser — those would silently fall back to GenericSourceParser and drop
    fixture conversion, so their ingest runs would create source_documents
    but no matches. Better to surface the mismatch on boot than discover
    it after a failed slate refresh."""
    try:
        from sqlalchemy import select

        from app.models.tables import SourceModel
        from app.parsers.registry import parser_registry

        session = db_session.SessionLocal()
        try:
            sources = session.scalars(select(SourceModel).where(SourceModel.is_active.is_(True))).all()
        finally:
            session.close()
        unknown: list[tuple[str, str]] = []
        for src in sources:
            if not parser_registry.has(src.parser_profile):
                unknown.append((src.name, src.parser_profile))
        if unknown:
            logger.warning(
                "found %d active source(s) with unregistered parser_profile (matches will not be persisted): %s. Known profiles: %s",
                len(unknown),
                unknown,
                parser_registry.known_profiles(),
            )
    except Exception as exc:  # pragma: no cover - non-fatal startup hook
        logger.warning("source/parser audit failed: %s", exc)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Base API for Progol analysis and prediction workflows.",
    lifespan=lifespan,
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url,
    openapi_url=settings.openapi_url,
)

if settings.allowed_hosts and "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

if settings.force_https:
    app.add_middleware(HTTPSRedirectMiddleware)

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "application error",
        extra={
            "event": "application_error",
            "path": request.url.path,
            "method": request.method,
            "status_code": exc.status_code,
            "detail": exc.message,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(IntegrityError)
async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    detail = str(exc.orig)
    logger.warning(
        "database integrity error",
        extra={
            "event": "database_integrity_error",
            "path": request.url.path,
            "method": request.method,
            "detail": detail,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    if "UNIQUE constraint failed" in detail:
        return JSONResponse(status_code=409, content={"detail": "The requested resource already exists."})
    return JSONResponse(status_code=400, content={"detail": "Database integrity error."})


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unexpected application error",
        extra={
            "event": "unexpected_application_error",
            "path": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


def _extract_api_key(request: Request) -> str | None:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _has_valid_session(request: Request) -> bool:
    token = request.cookies.get(settings.auth_session_cookie_name)
    return verify_session_token(token, secret=settings.session_secret)


def _is_auth_required(request: Request) -> bool:
    path = request.url.path
    if path != "/api" and not path.startswith("/api/"):
        return False
    if path in PUBLIC_API_PATHS:
        return False
    return True


def _client_key_for_rate_limit(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    if request.client:
        return request.client.host
    return "unknown"


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get(settings.request_id_header) or str(uuid4())
    request.state.request_id = request_id
    # Global rate limit (P11). Fires before auth so a hostile caller
    # can't burn auth-check CPU by hammering the endpoint either —
    # the throttle and the 401 path are both cheap, but the throttle
    # cuts off the cheapest one first.
    if settings.rate_limit_max_requests > 0 and (
        request.url.path == "/api" or request.url.path.startswith("/api/")
    ):
        from app.core.ratelimit import is_rate_limited, record_request

        client_key = _client_key_for_rate_limit(request)
        if is_rate_limited(
            client_key,
            window_seconds=settings.rate_limit_window_seconds,
            max_requests=settings.rate_limit_max_requests,
        ):
            logger.warning(
                "rate limit exceeded",
                extra={
                    "event": "rate_limit_exceeded",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "client": client_key,
                },
            )
            metrics_store.record_auth_failure(method=request.method, path=request.url.path)
            response = JSONResponse(status_code=429, content={"detail": "Too many requests."})
            response.headers[settings.request_id_header] = request_id
            response.headers["Retry-After"] = str(settings.rate_limit_window_seconds)
            return response
        record_request(client_key)
    if settings.auth_required and _is_auth_required(request):
        provided_api_key = _extract_api_key(request)
        api_key_valid = bool(provided_api_key) and secrets.compare_digest(
            provided_api_key or "",
            settings.auth_api_key or "",
        )
        if not api_key_valid and not _has_valid_session(request):
            logger.warning(
                "authentication failed",
                extra={
                    "event": "authentication_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "client": request.client.host if request.client else None,
                },
            )
            metrics_store.record_auth_failure(method=request.method, path=request.url.path)
            response = JSONResponse(status_code=401, content={"detail": "Authentication required."})
            response.headers[settings.request_id_header] = request_id
            return response
    started = perf_counter()
    response = await call_next(request)
    duration_ms = round((perf_counter() - started) * 1000, 2)
    metrics_store.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    response.headers[settings.request_id_header] = request_id
    if settings.access_log_enabled:
        logger.info(
            "request completed",
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else None,
            },
        )
    return response

app.include_router(health_router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(slates.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(tracking.router, prefix="/api")
app.include_router(ingestion.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(normalization.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.include_router(availability.router, prefix="/api")
app.include_router(scheduler.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(training.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(scoring.router, prefix="/api")
app.include_router(live_results.router, prefix="/api")
app.include_router(adaptive_dataset.router, prefix="/api")
app.include_router(training_adaptive.router, prefix="/api")
app.include_router(training_neural.router, prefix="/api")
app.include_router(learning.router, prefix="/api")
if settings.enable_worker_routes:
    app.include_router(worker.router, prefix="/api")

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    # Compute a single asset version hash from all JS/CSS at import time
    # so every deploy auto-rotates the cache-busting query string. The
    # hardcoded version we used previously stuck around across rebuilds
    # and silently served stale assets — caused user-visible bugs that
    # only a hard refresh could resolve.
    import hashlib as _hashlib

    def _compute_asset_version() -> str:
        h = _hashlib.sha256()
        for asset in sorted(frontend_dir.glob("*.js")) + sorted(frontend_dir.glob("*.css")):
            h.update(asset.read_bytes())
        return h.hexdigest()[:12]

    _asset_version = _compute_asset_version()
    _index_template = (frontend_dir / "index.html").read_text(encoding="utf-8")
    _index_rendered = _index_template.replace("__ASSET_VERSION__", _asset_version)

    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    async def serve_index() -> HTMLResponse:
        # index.html must never be cached: it carries the asset-version
        # query string that points at the current JS/CSS. A stale cached
        # index pins the browser to an old asset version, which can pair a
        # new app.js with an old helpers.js and break the ES module graph
        # (link error → app.js never runs → blank "Cargando…" UI). The
        # versioned assets themselves stay cacheable.
        return HTMLResponse(
            content=_index_rendered,
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    app.mount("/", StaticFiles(directory=frontend_dir, html=False), name="frontend")
