"""R6.0 — Money Mode endpoints (read-only, no writes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
async def test_slate_money_mode_endpoint_readonly(client, monkeypatch):
    """1 + 9-12 — slate money-mode responds and writes nothing, repeated 5x."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    before = _counts(db_mod.engine)
    resp = await client.get(f"/api/predictions/slates/{slate_id}/money-mode")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "money_mode_release_candidate"
    assert body["write_safety"] == {"writes_performed": False, "snapshots_created": False}
    assert set(body["tickets"]) == {"aggressive", "balanced", "conservative"}
    assert body["decision"]["status"]

    for _ in range(4):
        again = await client.get(f"/api/predictions/slates/{slate_id}/money-mode")
        assert again.status_code == 200
    assert _counts(db_mod.engine) == before


@pytest.mark.anyio
async def test_active_slates_money_mode_endpoint(client, monkeypatch):
    """3 — active_upcoming includes the seeded slate."""
    from app.db import session as db_mod

    enable_canary(monkeypatch, draws=[DRAW])
    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    resp = await client.get("/api/predictions/active-slates/money-mode")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "money_mode_release_candidate_active_upcoming"
    assert body["scope"] == "active_upcoming"
    assert any(s["slate"]["draw_code"] == DRAW for s in body["slates"])
    assert body["write_safety"]["writes_performed"] is False


@pytest.mark.anyio
async def test_money_mode_no_simple_never_simple_over_api(client, monkeypatch):
    """6 + 7 — over the API a NO-SIMPLE position is never a simple selection."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    body = (await client.get(f"/api/predictions/slates/{slate_id}/money-mode")).json()
    blocked = set(body["do_not_simple_positions"])
    for ticket in body["tickets"].values():
        for sel in ticket["selections"]:
            if sel["position"] in blocked:
                assert sel["type"] != "simple"


@pytest.mark.anyio
async def test_money_mode_endpoint_404_for_unknown_slate(client):
    """13 — 404 when the slate does not exist."""
    resp = await client.get("/api/predictions/slates/does-not-exist/money-mode")
    assert resp.status_code == 404
