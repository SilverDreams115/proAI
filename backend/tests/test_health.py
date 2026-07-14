import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _frontend_asset_text(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "frontend" / name).read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_health_endpoint_returns_ok(client) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_ready_endpoint_returns_ready(client) -> None:
    response = await client.get("/api/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["status"] == "ready"


def test_database_url_redaction_removes_password() -> None:
    from app.core.settings import redact_url_secret

    redacted = redact_url_secret("postgresql+psycopg://user:super-secret-value@postgres:5432/proai")

    assert "super-secret-value" not in redacted
    assert "user:***@postgres:5432" in redacted


def test_strict_production_config_rejects_placeholders(monkeypatch) -> None:
    from app.core import settings as settings_module

    settings_module.load_settings.cache_clear()
    monkeypatch.setenv("PROAI_ENVIRONMENT", "production")
    monkeypatch.setenv("PROAI_ENFORCE_PRODUCTION_CONFIG", "true")
    monkeypatch.setenv("PROAI_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PROAI_AUTH_API_KEY", "replace-with-a-strong-secret")
    monkeypatch.setenv("PROAI_DATABASE_URL", "postgresql+psycopg://proai:proai@postgres:5432/proai")
    monkeypatch.setenv("PROAI_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("PROAI_DOCS_ENABLED", "false")
    monkeypatch.setenv("PROAI_ENABLE_WORKER_ROUTES", "false")

    try:
        with pytest.raises(ValueError, match="Invalid production configuration"):
            settings_module.load_settings()
    finally:
        settings_module.load_settings.cache_clear()


@pytest.mark.anyio
async def test_password_login_sets_session_cookie(client, monkeypatch) -> None:
    from app.core.auth import hash_password
    from app.api.routes.auth import _login_failures
    from app.core.settings import settings

    _login_failures.clear()
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_api_key", "test-secret")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("correct-password"))
    monkeypatch.setattr(settings, "session_secret", "test-session-secret-value-with-enough-length")
    monkeypatch.setattr(settings, "force_https", False)

    unauthenticated = await client.get("/api/slates")
    unauthenticated_session = await client.get("/api/auth/session")
    failed_login = await client.post("/api/auth/login", json={"password": "wrong-password"})
    login = await client.post("/api/auth/login", json={"password": "correct-password"})
    session = await client.get("/api/auth/session")
    authenticated = await client.get("/api/slates")
    logout = await client.post("/api/auth/logout")
    after_logout = await client.get("/api/slates")

    assert unauthenticated.status_code == 401
    assert unauthenticated_session.status_code == 200
    assert unauthenticated_session.json()["authenticated"] is False
    assert failed_login.status_code == 401
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert session.status_code == 200
    assert session.json()["method"] == "session"
    assert authenticated.status_code == 200
    assert logout.status_code == 200
    assert after_logout.status_code == 401


@pytest.mark.anyio
async def test_password_login_throttles_repeated_failures(client, monkeypatch) -> None:
    from app.api.routes.auth import LOGIN_FAILURE_LIMIT
    from app.api.routes.auth import _login_failures
    from app.core.auth import hash_password
    from app.core.settings import settings

    _login_failures.clear()
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("correct-password"))
    monkeypatch.setattr(settings, "session_secret", "test-session-secret-value-with-enough-length")

    for _ in range(LOGIN_FAILURE_LIMIT):
        response = await client.post("/api/auth/login", json={"password": "wrong-password"})
        assert response.status_code == 401

    throttled = await client.post("/api/auth/login", json={"password": "correct-password"})

    assert throttled.status_code == 429


@pytest.mark.anyio
async def test_metrics_endpoint_exposes_prometheus_text(client) -> None:
    response = await client.get("/api/metrics")

    assert response.status_code == 200
    assert "proai_app_info" in response.text


@pytest.mark.anyio
async def test_auth_required_protects_safe_api_reads(client, monkeypatch) -> None:
    from app.core.settings import settings

    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_api_key", "test-secret")

    unauthenticated = await client.get("/api/slates")
    authenticated = await client.get("/api/slates", headers={"X-API-Key": "test-secret"})
    health = await client.get("/api/health")
    api_client_js = _frontend_asset_text("api-client.js")

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert health.status_code == 200
    assert "function loginWithPassword" in api_client_js


@pytest.mark.anyio
async def test_health_reads_persisted_worker_heartbeat(client, tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers import scheduler_worker

    polled_at = datetime.now(timezone.utc)
    executed_at = polled_at - timedelta(seconds=30)
    heartbeat_path = tmp_path / "worker-heartbeat.json"
    heartbeat_path.write_text(
        json.dumps(
            {
                "updated_at": polled_at.isoformat(),
                "executed_runs": 3,
                "failed_iterations": 0,
                "last_polled_at": polled_at.isoformat(),
                "last_executed_at": executed_at.isoformat(),
                "last_error_at": None,
                "last_error_message": None,
                "last_cycle_duration_ms": 12.5,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_worker, "WORKER_HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(settings, "health_worker_poll_warning_age_seconds", 120)
    monkeypatch.setattr(settings, "health_worker_poll_critical_age_seconds", 300)

    response = await client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_last_polled_at"] == polled_at.isoformat()
    assert payload["worker_last_executed_at"] == executed_at.isoformat()
    assert payload["worker_status"] == "executed"
    assert payload["worker_last_polled_age_seconds"] < 10


@pytest.mark.anyio
async def test_health_reports_stale_worker_poll(client, tmp_path, monkeypatch) -> None:
    from app.core.settings import settings
    from app.workers import scheduler_worker

    polled_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    heartbeat_path = tmp_path / "worker-heartbeat.json"
    heartbeat_path.write_text(
        json.dumps(
            {
                "updated_at": polled_at.isoformat(),
                "executed_runs": 0,
                "failed_iterations": 0,
                "last_polled_at": polled_at.isoformat(),
                "last_executed_at": None,
                "last_error_at": None,
                "last_error_message": None,
                "last_cycle_duration_ms": 4.2,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_worker, "WORKER_HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(settings, "health_worker_poll_warning_age_seconds", 60)
    monkeypatch.setattr(settings, "health_worker_poll_critical_age_seconds", 120)

    response = await client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["worker_status"] == "stale"
    assert payload["freshness_alerts"][0]["signal"] == "worker_poll"
    assert payload["freshness_alerts"][0]["severity"] == "critical"
