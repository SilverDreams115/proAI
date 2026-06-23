"""Active/upcoming slate scope generalization (R5.6-D).

The served policy must operate by rule on every active/upcoming slate, not on a
hardcoded draw_code. These tests lock that contract: active weekend + midweek
slates are in scope, archived ones are not, and a brand-new (future-cierre)
slate enters automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _seed_slate(session, *, draw_code, week_type, is_archived=False, closes_in_days=3):
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService

    closes = datetime.now(timezone.utc) + timedelta(days=closes_in_days)
    kickoff = datetime.now(timezone.utc) + timedelta(days=closes_in_days + 1)
    match = MatchReferencePayload(
        position=1,
        competition=CompetitionPayload(name="Test League", country="MX"),
        home_team=TeamPayload(name=f"{draw_code} H", country="MX"),
        away_team=TeamPayload(name=f"{draw_code} A", country="MX"),
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
async def test_scope_includes_active_weekend_and_midweek_excludes_archived(client):
    from app.db.session import SessionLocal
    from app.services.active_slate_scope import build_active_slate_scope

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PG-2338", week_type="weekend")
        _seed_slate(session, draw_code="PGM-801", week_type="midweek")
        _seed_slate(session, draw_code="PGM-OLD", week_type="midweek", is_archived=True)

    with SessionLocal() as session:
        scope = build_active_slate_scope(session)

    codes = {s.draw_code for s in scope}
    assert "PG-2338" in codes
    assert "PGM-801" in codes
    assert "PGM-OLD" not in codes  # archived never enters active scope
    assert all(s.is_archived is False for s in scope)
    assert all(s.status == "active_upcoming" for s in scope)


@pytest.mark.anyio
async def test_future_slate_enters_scope_by_rule(client):
    """A future slate enters automatically — no hardcoded draw_code."""
    from app.db.session import SessionLocal
    from app.services.active_slate_scope import build_active_slate_scope

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PG-9999", week_type="weekend", closes_in_days=10)

    with SessionLocal() as session:
        scope = build_active_slate_scope(session)

    assert "PG-9999" in {s.draw_code for s in scope}


@pytest.mark.anyio
async def test_closed_unarchived_slate_not_in_scope(client):
    """A slate whose cierre already passed is not active even if not archived."""
    from app.db.session import SessionLocal
    from app.services.active_slate_scope import build_active_slate_scope

    with SessionLocal() as session:
        _seed_slate(session, draw_code="PG-PAST", week_type="weekend", closes_in_days=-2)

    with SessionLocal() as session:
        scope = build_active_slate_scope(session)

    assert "PG-PAST" not in {s.draw_code for s in scope}
