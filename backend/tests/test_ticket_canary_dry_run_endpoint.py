"""R5.7 — ticket canary dry-run endpoints (read-only)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)
from backend.tests.test_ticket_canary_dry_run_service import DRAW, enable_canary, seed_canary_slate


def _counts(engine):
    with Session(engine) as s:
        return (
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
            int(s.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0),
            int(s.scalar(select(func.count()).select_from(TicketRecommendationSnapshotModel)) or 0),
        )


@pytest.mark.anyio
async def test_slate_dry_run_endpoint_readonly(client, monkeypatch):
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    before = _counts(db_mod.engine)
    resp = await client.get(f"/api/predictions/slates/{slate_id}/ticket-canary-dry-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "ticket_canary_dry_run"
    assert body["write_safety"]["writes_performed"] is False
    assert body["slate"]["draw_code"] == DRAW

    for _ in range(4):
        again = await client.get(f"/api/predictions/slates/{slate_id}/ticket-canary-dry-run")
        assert again.status_code == 200
    assert _counts(db_mod.engine) == before


@pytest.mark.anyio
async def test_active_slates_dry_run_endpoint(client, monkeypatch):
    from app.db import session as db_mod
    from datetime import datetime, timedelta, timezone

    enable_canary(monkeypatch, draws=[DRAW])
    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        # Make it active/upcoming so the active_upcoming scope includes it.
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    resp = await client.get("/api/predictions/active-slates/ticket-canary-dry-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "ticket_canary_dry_run_active_upcoming"
    assert any(s["slate"]["draw_code"] == DRAW for s in body["slates"])
    assert body["write_safety"]["writes_performed"] is False


@pytest.mark.anyio
async def test_dry_run_endpoint_404_for_unknown_slate(client):
    resp = await client.get("/api/predictions/slates/does-not-exist/ticket-canary-dry-run")
    assert resp.status_code == 404
