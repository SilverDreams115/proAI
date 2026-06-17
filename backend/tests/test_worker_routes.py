"""
Tests for worker route auth hardening (QW-3).

Worker routes are only registered when PROAI_ENABLE_WORKER_ROUTES=true
(set in conftest.py for all tests). The guard in require_worker_auth:
  - passes silently when no credentials are configured (bare-dev posture)
  - enforces API-key/session auth as soon as any credential is set
"""

import pytest

from app.core.settings import settings


# ---------------------------------------------------------------------------
# Bare-dev posture (no credentials) — guard is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_worker_status_open_when_no_credentials(client) -> None:
    response = await client.get("/api/worker/scheduler/status")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_worker_run_once_open_when_no_credentials(client) -> None:
    response = await client.post("/api/worker/scheduler/run-once")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Credentials configured — guard enforces auth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_worker_status_rejects_unauthenticated_when_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.get("/api/worker/scheduler/status")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.anyio
async def test_worker_run_once_rejects_unauthenticated_when_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.post("/api/worker/scheduler/run-once")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.anyio
async def test_worker_status_accepts_valid_api_key(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.get(
        "/api/worker/scheduler/status",
        headers={"X-API-Key": "test-worker-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "executed_runs" in payload
    assert "last_polled_at" in payload


@pytest.mark.anyio
async def test_worker_run_once_accepts_valid_api_key(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.post(
        "/api/worker/scheduler/run-once",
        headers={"X-API-Key": "test-worker-secret"},
    )

    assert response.status_code == 200
    assert "executed_runs" in response.json()


@pytest.mark.anyio
async def test_worker_status_accepts_bearer_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.get(
        "/api/worker/scheduler/status",
        headers={"Authorization": "Bearer test-worker-secret"},
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_worker_run_once_rejects_wrong_key(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-worker-secret")

    response = await client.post(
        "/api/worker/scheduler/run-once",
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Other protected routes still work (regression)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_slates_route_unaffected_by_worker_hardening(client) -> None:
    response = await client.get("/api/slates")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_health_route_unaffected_by_worker_hardening(client) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
