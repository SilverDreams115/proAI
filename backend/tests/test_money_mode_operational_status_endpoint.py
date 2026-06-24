"""R6.1 — operational Money Mode status endpoint (read-only, no writes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
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
async def test_status_endpoint_reports_active_slate(client, monkeypatch):
    """5 + 7 + 8 — endpoint responds, surfaces the slate decision, writes nothing."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    before = _counts(db_mod.engine)
    resp = await client.get("/api/operations/money-mode/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "money_mode_operational_status"
    assert body["write_safety"]["read_only"] is True
    assert body["active_slate_count"] >= 1
    entry = next(s for s in body["slates"] if s["draw_code"] == DRAW)
    assert entry["decision"]  # decision present (JUGAR/NO JUGAR)
    assert "money_mode_ready" in entry
    # 6 — blocked_slate_count exposed and consistent.
    assert body["blocked_slate_count"] == body["active_slate_count"] - body["playable_slate_count"]

    for _ in range(4):
        again = await client.get("/api/operations/money-mode/status")
        assert again.status_code == 200
    assert _counts(db_mod.engine) == before  # 8 — no writes


@pytest.mark.anyio
async def test_status_endpoint_empty_when_no_active_slates(client, monkeypatch):
    """9 — empty/zero state when there are no active/upcoming slates."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        # seed leaves past kickoffs and no registration cierre -> closed/not active.
        seed_canary_slate(session)

    resp = await client.get("/api/operations/money-mode/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_slate_count"] == 0
    assert body["playable_slate_count"] == 0
    assert body["blocked_slate_count"] == 0
    assert body["slates"] == []
