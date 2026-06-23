"""Multi-week slate coexistence — Weekend + Midweek/MS.

Tests that:
- GET /api/slates lists slates with has_predictions / has_valid_snapshot / status_label
- include_closed=true returns archived slates alongside active ones
- Weekend and midweek slates are independent — selecting one does not bleed data
  from the other
- Predictions saved for a midweek slate use that slate's own slate_id
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(tmp_path: Path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "multi_week.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_slate(session, *, draw_code: str, week_type: str, is_archived: bool = False):
    """Create a minimal progol_slate row directly via the service."""
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.slate import ProgolSlateCreate
    from app.schemas.common import MatchReferencePayload, TeamPayload, CompetitionPayload
    from app.services.slate_service import SlateService

    # Use far-future dates so the seeded slate stays "active" (not closed)
    # regardless of the wall clock. A fixed near-term date rots: once the
    # registration_closes_at timestamp passes, the slate is treated as
    # closed and hidden from the default /api/slates listing, which made
    # these tests fail by date rather than by logic. Mirrors the 2099
    # dates already used in test_default_list_excludes_archived_and_closed.
    match = MatchReferencePayload(
        position=1,
        competition=CompetitionPayload(name="Test League", country="MX"),
        home_team=TeamPayload(name="Home FC", country="MX"),
        away_team=TeamPayload(name="Away FC", country="MX"),
        kickoff_at=datetime(2099, 6, 20, 18, 0, tzinfo=timezone.utc),
    )
    payload = ProgolSlateCreate(
        label=f"Slate {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
        registration_closes_at=datetime(2099, 6, 19, 3, 0, tzinfo=timezone.utc),
        is_archived=is_archived,
        matches=[match],
    )
    service = SlateService(SlateRepository(session))
    slate = service.create_slate(payload)
    session.commit()
    return slate


# ---------------------------------------------------------------------------
# Tests: status fields on the API response
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_slates_includes_status_fields(client) -> None:
    """GET /api/slates returns has_predictions, has_valid_snapshot, status_label."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        _seed_slate(session, draw_code="PG-TEST-WK", week_type="weekend")
    finally:
        session.close()

    response = await client.get("/api/slates")
    assert response.status_code == 200
    slates = response.json()
    assert len(slates) >= 1
    slate = next((s for s in slates if s["draw_code"] == "PG-TEST-WK"), None)
    assert slate is not None, "PG-TEST-WK should be in the list"
    assert "has_predictions" in slate
    assert "has_valid_snapshot" in slate
    assert "status_label" in slate
    assert slate["has_predictions"] is False
    assert slate["has_valid_snapshot"] is False
    # R5.6 hotfix: an active slate with matches but no persisted predictions
    # reads "Predicción live" (served read-only on demand), never a false
    # "Sin predicción".
    assert slate["status_label"] == "Predicción live"
    assert slate["prediction_status"] == "live_available"


@pytest.mark.anyio
async def test_list_slates_include_closed_returns_archived(client) -> None:
    """include_closed=true returns archived slates that default listing hides."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        _seed_slate(session, draw_code="PGM-TEST-MS", week_type="midweek", is_archived=True)
    finally:
        session.close()

    # Default listing should NOT include the archived slate.
    default_resp = await client.get("/api/slates")
    assert default_resp.status_code == 200
    codes_default = [s["draw_code"] for s in default_resp.json()]
    assert "PGM-TEST-MS" not in codes_default

    # With include_closed=true it MUST appear.
    full_resp = await client.get("/api/slates?include_closed=true")
    assert full_resp.status_code == 200
    codes_full = [s["draw_code"] for s in full_resp.json()]
    assert "PGM-TEST-MS" in codes_full
    ms_slate = next(s for s in full_resp.json() if s["draw_code"] == "PGM-TEST-MS")
    assert ms_slate["week_type"] == "midweek"
    assert ms_slate["is_archived"] is True
    assert ms_slate["status_label"] == "Archivada"


@pytest.mark.anyio
async def test_weekend_and_midweek_slates_coexist(client) -> None:
    """Weekend and midweek slates both appear in the same list with their own week_type."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        _seed_slate(session, draw_code="PG-WK-001", week_type="weekend")
        _seed_slate(session, draw_code="PGM-MS-001", week_type="midweek")
    finally:
        session.close()

    response = await client.get("/api/slates")
    assert response.status_code == 200
    slates = response.json()
    draw_codes = {s["draw_code"] for s in slates}
    assert "PG-WK-001" in draw_codes
    assert "PGM-MS-001" in draw_codes

    wk = next(s for s in slates if s["draw_code"] == "PG-WK-001")
    ms = next(s for s in slates if s["draw_code"] == "PGM-MS-001")
    assert wk["week_type"] == "weekend"
    assert ms["week_type"] == "midweek"


@pytest.mark.anyio
async def test_get_slate_by_id_returns_correct_week_type(client) -> None:
    """GET /api/slates/{id} returns the exact slate requested, not any other."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        wk = _seed_slate(session, draw_code="PG-WK-002", week_type="weekend")
        ms = _seed_slate(session, draw_code="PGM-MS-002", week_type="midweek")
        wk_id = wk.id
        ms_id = ms.id
    finally:
        session.close()

    wk_resp = await client.get(f"/api/slates/{wk_id}")
    assert wk_resp.status_code == 200
    assert wk_resp.json()["draw_code"] == "PG-WK-002"
    assert wk_resp.json()["week_type"] == "weekend"

    ms_resp = await client.get(f"/api/slates/{ms_id}")
    assert ms_resp.status_code == 200
    assert ms_resp.json()["draw_code"] == "PGM-MS-002"
    assert ms_resp.json()["week_type"] == "midweek"


@pytest.mark.anyio
async def test_predictions_for_ms_slate_use_ms_slate_id(client) -> None:
    """Predictions fetched for a midweek slate are scoped to that slate only.

    GET /api/predictions/slates/{ms_id} must not return predictions that
    belong to a weekend slate, and vice-versa."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        ms = _seed_slate(session, draw_code="PGM-MS-003", week_type="midweek")
        ms_id = ms.id
    finally:
        session.close()

    # No predictions yet — the endpoint returns an empty list (no 500).
    response = await client.get(f"/api/predictions/slates/{ms_id}")
    assert response.status_code == 200
    data = response.json()
    # All returned predictions must reference matches on THIS slate.
    from app.db.session import SessionLocal as SL2
    session2 = SL2()
    try:
        from sqlalchemy import select
        from app.models.tables import ProgolSlateMatchModel
        ms_match_ids = {
            row.match_id for row in session2.scalars(
                select(ProgolSlateMatchModel).where(ProgolSlateMatchModel.slate_id == ms_id)
            )
        }
    finally:
        session2.close()
    for pred in data:
        assert pred["match_id"] in ms_match_ids, (
            f"Prediction for {pred['match_id']} does not belong to midweek slate {ms_id}"
        )


@pytest.mark.anyio
async def test_status_label_with_ticket_snapshot(client) -> None:
    """A slate with a valid snapshot shows status_label='Con ticket'."""
    from app.db.session import SessionLocal
    from app.models.tables import TicketRecommendationSnapshotModel

    session = SessionLocal()
    try:
        wk = _seed_slate(session, draw_code="PG-WK-003", week_type="weekend")
        # Manually inject a valid snapshot.
        snap = TicketRecommendationSnapshotModel(
            slate_id=wk.id,
            model_version="ticket-optimizer-test",
            payload_json="{}",
            composition_hash=wk.composition_hash,
            is_valid=True,
        )
        session.add(snap)
        session.commit()
        wk_id = wk.id
    finally:
        session.close()

    response = await client.get(f"/api/slates/{wk_id}")
    assert response.status_code == 200
    assert response.json()["has_valid_snapshot"] is True
    assert response.json()["status_label"] == "Con ticket"


@pytest.mark.anyio
async def test_default_list_excludes_archived_and_closed(client) -> None:
    """GET /api/slates (no params) must never return archived or closed slates.

    This guards against regressions like accidentally passing include_closed=true
    from the frontend and surfacing junk/historical slates in the main selector."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        future_close = datetime(2099, 12, 31, 23, 59, tzinfo=timezone.utc)
        from app.repositories.slate_repository import SlateRepository
        from app.schemas.slate import ProgolSlateCreate
        from app.schemas.common import MatchReferencePayload, TeamPayload, CompetitionPayload
        from app.services.slate_service import SlateService

        def _mk_match(pos):
            return MatchReferencePayload(
                position=pos,
                competition=CompetitionPayload(name="TL", country="MX"),
                home_team=TeamPayload(name=f"H{pos}", country="MX"),
                away_team=TeamPayload(name=f"A{pos}", country="MX"),
                kickoff_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
            )

        svc = SlateService(SlateRepository(session))
        svc.create_slate(ProgolSlateCreate(
            label="Active WK",
            draw_code="PG-ACTIVE-CLEAN",
            week_type="weekend",
            registration_closes_at=future_close,
            matches=[_mk_match(1)],
        ))
        svc.create_slate(ProgolSlateCreate(
            label="Archived WK",
            draw_code="PG-ARCHIVED-CLEAN",
            week_type="weekend",
            registration_closes_at=future_close,
            is_archived=True,
            matches=[_mk_match(1)],
        ))
        past_close = datetime(2020, 1, 1, tzinfo=timezone.utc)
        svc.create_slate(ProgolSlateCreate(
            label="Closed WK",
            draw_code="PG-CLOSED-CLEAN",
            week_type="weekend",
            registration_closes_at=past_close,
            matches=[_mk_match(1)],
        ))
        session.commit()
    finally:
        session.close()

    resp = await client.get("/api/slates")
    assert resp.status_code == 200
    codes = [s["draw_code"] for s in resp.json()]
    assert "PG-ACTIVE-CLEAN" in codes, "Active slate must appear"
    assert "PG-ARCHIVED-CLEAN" not in codes, "Archived must be hidden"
    assert "PG-CLOSED-CLEAN" not in codes, "Closed must be hidden"


@pytest.mark.anyio
async def test_active_weekend_visible_ms_empty(client) -> None:
    """When only a weekend slate exists, GET /api/slates returns just that slate
    and the midweek group is absent — the UI empty-state handles this gracefully."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        _seed_slate(session, draw_code="PG-WK-CLEAN-ONLY", week_type="weekend")
    finally:
        session.close()

    resp = await client.get("/api/slates")
    assert resp.status_code == 200
    slates = resp.json()
    week_types = {s["week_type"] for s in slates}
    assert "weekend" in week_types
    # No midweek slate was seeded — the list must not invent one.
    assert all(s["week_type"] != "midweek" or s["draw_code"] == "PGM-TEST-MS"
               for s in slates), "No unexpected midweek slate should appear"
