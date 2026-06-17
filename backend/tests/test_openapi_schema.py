"""
Tests for /api/openapi-schema auth hardening.

The endpoint must:
- be open in bare-dev (no credentials configured)
- require auth when credentials are configured, regardless of auth_required flag
- not interfere with /health, /ready, or other public routes
"""

import pytest

from app.core.settings import settings


# ---------------------------------------------------------------------------
# Bare-dev posture (no credentials) — guard is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_openapi_schema_open_when_no_credentials(client) -> None:
    response = await client.get("/api/openapi-schema")
    assert response.status_code == 200
    body = response.json()
    assert "openapi" in body
    assert "paths" in body


# ---------------------------------------------------------------------------
# Credentials configured — guard enforces auth
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_openapi_schema_rejects_unauthenticated_when_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get("/api/openapi-schema")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.anyio
async def test_openapi_schema_accepts_valid_api_key(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get(
        "/api/openapi-schema",
        headers={"X-API-Key": "test-schema-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "openapi" in body
    assert "paths" in body


@pytest.mark.anyio
async def test_openapi_schema_accepts_bearer_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get(
        "/api/openapi-schema",
        headers={"Authorization": "Bearer test-schema-secret"},
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_openapi_schema_rejects_wrong_key(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get(
        "/api/openapi-schema",
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Public routes not affected (regression)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_still_public_when_schema_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


@pytest.mark.anyio
async def test_ready_still_public_when_schema_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get("/api/ready")

    assert response.status_code in {200, 503}


@pytest.mark.anyio
async def test_metrics_still_public_when_schema_credentials_set(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "auth_api_key", "test-schema-secret")

    response = await client.get("/api/metrics")

    assert response.status_code == 200
