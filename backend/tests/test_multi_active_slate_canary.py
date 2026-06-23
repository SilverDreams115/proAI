"""Canary scope generalization across multiple active slates (R5.6-D).

The canary must be rule-gated: under ``active_upcoming`` scope it considers
every active/upcoming slate (weekend + midweek), never archived ones, and an
optional draw_code allowlist still restricts it. Per-position gating is covered
elsewhere; here we lock the *scope* decision so it is not hardcoded to PG-2338.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core import settings as settings_module
from app.services.team_rating_canary_service import _slate_in_canary_scope, compute_canary_plan


def _seed_slate(session, *, draw_code, week_type, is_archived=False, closes_in_days=3):
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService

    closes = datetime.now(timezone.utc) + timedelta(days=closes_in_days)
    match = MatchReferencePayload(
        position=1,
        competition=CompetitionPayload(name="International Friendlies", country="World"),
        home_team=TeamPayload(name=f"{draw_code} H", country="MX"),
        away_team=TeamPayload(name=f"{draw_code} A", country="MX"),
        kickoff_at=closes + timedelta(days=1),
    )
    payload = ProgolSlateCreate(
        label=f"Slate {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
        registration_closes_at=closes,
        is_archived=is_archived,
        matches=[match],
    )
    return SlateService(SlateRepository(session)).create_slate(payload)


@pytest.fixture
def _set_canary(monkeypatch):
    def _apply(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(settings_module.settings, k, v, raising=False)
    return _apply


@pytest.mark.anyio
async def test_active_upcoming_scope_covers_both_active_excludes_archived(client, _set_canary):
    from app.db.session import SessionLocal

    _set_canary(team_rating_canary_scope="active_upcoming", team_rating_canary_draw_codes=[])
    with SessionLocal() as session:
        wk = _seed_slate(session, draw_code="PG-2338", week_type="weekend")
        ms = _seed_slate(session, draw_code="PGM-801", week_type="midweek")
        old = _seed_slate(session, draw_code="PGM-OLD", week_type="midweek", is_archived=True)
        session.expire_all()
        assert _slate_in_canary_scope(session, wk, wk.draw_code) is True
        assert _slate_in_canary_scope(session, ms, ms.draw_code) is True
        assert _slate_in_canary_scope(session, old, old.draw_code) is False


@pytest.mark.anyio
async def test_active_upcoming_with_allowlist_restricts(client, _set_canary):
    from app.db.session import SessionLocal

    _set_canary(
        team_rating_canary_scope="active_upcoming",
        team_rating_canary_draw_codes=["PGM-801"],
    )
    with SessionLocal() as session:
        wk = _seed_slate(session, draw_code="PG-2338", week_type="weekend")
        ms = _seed_slate(session, draw_code="PGM-801", week_type="midweek")
        assert _slate_in_canary_scope(session, ms, ms.draw_code) is True
        assert _slate_in_canary_scope(session, wk, wk.draw_code) is False  # not in allowlist


@pytest.mark.anyio
async def test_draw_code_allowlist_scope_default(client, _set_canary):
    from app.db.session import SessionLocal

    _set_canary(
        team_rating_canary_scope="draw_code_allowlist",
        team_rating_canary_draw_codes=["PG-2338"],
    )
    with SessionLocal() as session:
        wk = _seed_slate(session, draw_code="PG-2338", week_type="weekend")
        ms = _seed_slate(session, draw_code="PGM-801", week_type="midweek")
        assert _slate_in_canary_scope(session, wk, wk.draw_code) is True
        assert _slate_in_canary_scope(session, ms, ms.draw_code) is False


@pytest.mark.anyio
async def test_canary_disabled_yields_no_active_positions(client, _set_canary):
    from app.db.session import SessionLocal

    _set_canary(
        team_rating_canary_enabled=False,
        team_rating_canary_scope="active_upcoming",
        team_rating_canary_draw_codes=[],
        team_rating_canary_positions=[1],
    )
    with SessionLocal() as session:
        ms = _seed_slate(session, draw_code="PGM-801", week_type="midweek")
        plan = compute_canary_plan(session, ms)
        assert plan.enabled is False
        assert plan.active_positions == []
