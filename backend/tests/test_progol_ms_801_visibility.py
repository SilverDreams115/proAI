"""Progol MS / PGM visibility regression (PGM-801 scenario).

PGM-801 is a midweek (Media Semana) concurso. The main sidebar shows only
active/upcoming concursos (default ``/api/slates``); archived jornadas are
reachable only via the explicit ``/api/slates?include_closed=true`` history
query and are never mixed into the main list. These tests lock that contract:

- an active (non-archived, future-cierre) MS like the corrected PGM-801 appears
  in the DEFAULT listing alongside the active weekend slate;
- an archived MS is hidden from the default listing but returned with
  ``include_closed=true`` (so it stays reachable as history);
- when an active weekend slate coexists with an archived MS, the default list
  contains only the active slate and the open slate sorts FIRST.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _seed_slate(session, *, draw_code: str, week_type: str, is_archived: bool = False):
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService

    # Far-future dates keep a non-archived slate "open" regardless of wall clock.
    closes = datetime.now(timezone.utc) + timedelta(days=3)
    kickoff = datetime.now(timezone.utc) + timedelta(days=4)
    match = MatchReferencePayload(
        position=1,
        competition=CompetitionPayload(name="Test League", country="MX"),
        home_team=TeamPayload(name=f"{draw_code} Home", country="MX"),
        away_team=TeamPayload(name=f"{draw_code} Away", country="MX"),
        kickoff_at=kickoff,
    )
    payload = ProgolSlateCreate(
        label=f"Slate {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
        registration_closes_at=closes,
        is_archived=is_archived,
        matches=[match],
    )
    SlateService(SlateRepository(session)).create_slate(payload)


@pytest.mark.anyio
async def test_archived_ms_slate_hidden_by_default_visible_with_include_closed(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", is_archived=True)

    default = await client.get("/api/slates")
    assert default.status_code == 200
    assert "PGM-801" not in {s["draw_code"] for s in default.json()}

    full = await client.get("/api/slates?include_closed=true")
    assert full.status_code == 200
    ms = {s["draw_code"]: s for s in full.json()}
    assert "PGM-801" in ms
    assert ms["PGM-801"]["week_type"] == "midweek"
    assert ms["PGM-801"]["is_archived"] is True
    assert ms["PGM-801"]["status_label"] == "Archivada"


@pytest.mark.anyio
async def test_active_weekend_sorts_before_archived_ms(client):
    """The frontend auto-selects state.slates[0]; it must stay the open slate."""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", is_archived=True)
        _seed_slate(session, draw_code="PG-2338", week_type="weekend", is_archived=False)

    full = await client.get("/api/slates?include_closed=true")
    assert full.status_code == 200
    codes = [s["draw_code"] for s in full.json()]
    assert codes[0] == "PG-2338"  # open weekend slate stays first / auto-selected
    assert "PGM-801" in codes  # archived MS still reachable in the list

    # Default listing still shows the active weekend slate and only it.
    default_codes = [s["draw_code"] for s in (await client.get("/api/slates")).json()]
    assert default_codes == ["PG-2338"]


@pytest.mark.anyio
async def test_active_ms_slate_appears_in_default_list(client):
    """An active (non-archived, future-cierre) Progol MS — e.g. the corrected
    PGM-801 — must appear in the DEFAULT /api/slates list alongside the active
    weekend slate, while an archived MS stays out of the default view."""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PG-2338", week_type="weekend", is_archived=False)
        _seed_slate(session, draw_code="PGM-801", week_type="midweek", is_archived=False)
        _seed_slate(session, draw_code="PGM-800", week_type="midweek", is_archived=True)

    default = await client.get("/api/slates")
    assert default.status_code == 200
    codes = {s["draw_code"] for s in default.json()}
    assert "PGM-801" in codes  # active MS visible in the main list
    assert "PG-2338" in codes  # active weekend still visible
    assert "PGM-800" not in codes  # archived MS NOT mixed into the main list

    ms = next(s for s in default.json() if s["draw_code"] == "PGM-801")
    assert ms["week_type"] == "midweek"
    assert ms["is_archived"] is False
