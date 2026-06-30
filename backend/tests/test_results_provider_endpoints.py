"""R6.3 — results provider dry-run endpoints (read-only, no writes)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import MatchResultModel, PredictionModel, ProgolSlateModel
from backend.tests.test_ticket_canary_dry_run_service import DRAW, seed_canary_slate


def _counts(engine):
    with Session(engine) as s:
        return (
            int(s.scalar(select(func.count()).select_from(MatchResultModel)) or 0),
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
        )


@pytest.mark.anyio
async def test_slate_provider_dry_run_endpoint(client):
    """3 + 6 — endpoint responds read-only and writes nothing."""
    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    before = _counts(db_mod.engine)
    resp = await client.get(f"/api/results/slates/{slate_id}/provider-dry-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "results_provider_dry_run"
    assert body["write_safety"]["writes_performed"] is False
    assert "coverage" in body
    assert _counts(db_mod.engine) == before


@pytest.mark.anyio
async def test_active_slates_provider_dry_run(client):
    """4 — active-upcoming provider dry-run lists the active slate(s)."""
    from datetime import datetime, timedelta, timezone

    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    resp = await client.get("/api/results/active-slates/provider-dry-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "results_provider_dry_run_active_upcoming"
    assert any(s["slate"]["draw_code"] == DRAW for s in body["slates"])
    assert body["write_safety"]["writes_performed"] is False


@pytest.mark.anyio
async def test_provider_dry_run_404(client):
    """12 — 404 for an unknown slate."""
    resp = await client.get("/api/results/slates/does-not-exist/provider-dry-run")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_dashboard_fast_endpoint(client):
    """CP4 — dashboard-fast responds without computing money-mode, read-only."""
    from datetime import datetime, timedelta, timezone

    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    resp = await client.get("/api/operations/dashboard-fast")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "dashboard_fast"
    assert body["active_slate_count"] >= 1
    assert body["default_slate_id"]
    assert body["write_safety"]["read_only"] is True
