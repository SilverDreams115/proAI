"""index.html must be served uncacheable so the asset-version query string
always points at the current JS/CSS.

A cached index pins the browser to a stale asset version, which can pair a
new app.js with an old helpers.js and break the ES module graph (link
error → app.js never runs → blank "Cargando…" UI). This regression test
locks the no-store header in place.
"""
from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_index_served_with_no_store(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "no-store" in cache_control
    # The asset-version placeholder must be substituted, not served raw.
    assert "__ASSET_VERSION__" not in resp.text
