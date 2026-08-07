"""The hardening headers must travel with the app, not with the proxy.

deploy/Caddyfile sets the same headers, but it only fronts the production
compose file. The app is routinely operated straight from
docker-compose.yml, which publishes uvicorn on :8000 with nothing in front
— and that instance answered with no CSP, no frame protection and no
nosniff. These tests lock the guarantee to the application itself, so it
holds under either deployment topology.
"""
from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_dashboard_carries_the_hardening_headers(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.anyio
async def test_csp_forbids_inline_script_and_framing(client):
    resp = await client.get("/")
    csp = resp.headers["content-security-policy"]
    # The frontend ships no inline scripts and no on* handlers, so the
    # strict directive costs nothing — and must not be relaxed silently.
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


@pytest.mark.anyio
async def test_api_responses_carry_the_headers_too(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.anyio
async def test_rejected_requests_are_hardened_as_well(client):
    # An unauthenticated 401 short-circuits before the normal response
    # path; it must not lose the headers on the way out.
    resp = await client.get("/api/predictions/slates/does-not-exist")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in resp.headers


@pytest.mark.anyio
async def test_hsts_absent_without_tls(client):
    # Pinning a browser to https from a plain-HTTP deployment would lock
    # the operator out of their own dashboard.
    resp = await client.get("/")
    assert "strict-transport-security" not in resp.headers
