"""Active-slate selection metadata (R5.6 hotfix).

The slate list must give the UI enough read-only metadata to (a) keep both
active slates selectable and (b) never show a false "Sin predicción" for an
active slate whose predictions can be served live on demand.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select


def _seed_slate(session, *, draw_code, week_type, is_archived=False, closes_in_days=3, n=1):
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService

    closes = datetime.now(timezone.utc) + timedelta(days=closes_in_days)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="International Friendlies", country="World"),
            home_team=TeamPayload(name=f"{draw_code} H{i}", country="MX"),
            away_team=TeamPayload(name=f"{draw_code} A{i}", country="MX"),
            kickoff_at=closes + timedelta(days=1, hours=i),
        )
        for i in range(1, n + 1)
    ]
    payload = ProgolSlateCreate(
        label=f"Slate {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
        registration_closes_at=closes,
        is_archived=is_archived,
        matches=matches,
    )
    return SlateService(SlateRepository(session)).create_slate(payload)


@pytest.mark.anyio
async def test_both_active_slates_listed_with_metadata(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PG-2338", week_type="weekend", n=14)
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", n=9)
        _seed_slate(session, draw_code="PGM-OLD", week_type="midweek", is_archived=True, n=9)

    resp = await client.get("/api/slates")
    assert resp.status_code == 200
    by_code = {s["draw_code"]: s for s in resp.json()}
    assert "PG-2338" in by_code and "PGM-801" in by_code
    assert "PGM-OLD" not in by_code  # archived not mixed into the main list

    ms = by_code["PGM-801"]
    assert ms["is_archived"] is False
    assert ms["match_count"] == 9


@pytest.mark.anyio
async def test_active_ms_without_persisted_is_live_not_sin_prediccion(client):
    """PGM-801 (active, 0 persisted) must read live_available, never sin_prediccion."""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", n=9)

    ms = next(s for s in (await client.get("/api/slates")).json() if s["draw_code"] == "PGM-801")
    assert ms["persisted_prediction_count"] == 0
    assert ms["live_prediction_available"] is True
    assert ms["prediction_status"] == "live_available"
    assert ms["status_label"] != "Sin predicción"
    assert ms["status_label"] == "Predicción live"


@pytest.mark.anyio
async def test_get_slates_writes_nothing(client):
    """Listing slates must not create predictions or snapshots."""
    from app.db.session import SessionLocal
    from app.models.tables import PredictionModel, TicketRecommendationSnapshotModel

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", n=9)

    def _counts():
        with SessionLocal() as s:
            return (
                int(s.scalar(select(func.count(PredictionModel.id))) or 0),
                int(s.scalar(select(func.count(TicketRecommendationSnapshotModel.id))) or 0),
            )

    before = _counts()
    await client.get("/api/slates")
    await client.get("/api/slates?include_closed=true")
    assert _counts() == before
