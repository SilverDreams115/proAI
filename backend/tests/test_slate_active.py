"""Auto-transition tests (Fase 1.4).

The `/api/slates/active` endpoint and the worker's cierre-archival job
together implement automatic slate rotation: when one Progol contest's
registration window closes, the next one becomes the answer to
`/slates/active`. These tests pin the two pieces:

  * `archive_due_slates` flips `is_archived=true` for slates whose
    `registration_closes_at` has passed.
  * `/slates/active` always returns the open slate with the *closest*
    future cierre, never a closed one.

The fixtures stay close to a real round of Progol — one midweek slate
(closing tomorrow) and one weekend slate (closing in two days) — so a
break in either component is easy to spot from the assertion text.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _slate_payload(*, draw_code: str, week_type: str, closes_at: datetime, kickoff_at: datetime) -> dict:
    return {
        "label": f"Probe {draw_code}",
        "draw_code": draw_code,
        "week_type": week_type,
        "registration_closes_at": closes_at.isoformat(),
        "matches": [
            {
                "position": 1,
                "competition": {"name": "Liga MX", "country": "Mexico", "season": "2026-C"},
                "home_team": {"name": "Club A", "country": "Mexico"},
                "away_team": {"name": "Club B", "country": "Mexico"},
                "kickoff_at": kickoff_at.isoformat(),
                "venue": "Sample Stadium",
            }
        ],
    }


@pytest.mark.anyio
async def test_active_endpoint_returns_slate_with_closest_future_cierre(client) -> None:
    """Two open slates, one closing tomorrow and one in two days. The
    endpoint must pick the closer cierre — that's the operator's next
    deadline and the one the model should be predicting for."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=6)
    later = now + timedelta(days=2)

    for payload in (
        _slate_payload(
            draw_code="PG-LATER",
            week_type="weekend",
            closes_at=later,
            kickoff_at=later + timedelta(hours=4),
        ),
        _slate_payload(
            draw_code="PGM-SOON",
            week_type="midweek",
            closes_at=soon,
            kickoff_at=soon + timedelta(hours=4),
        ),
    ):
        response = await client.post("/api/slates", json=payload)
        assert response.status_code == 201, response.text

    response = await client.get("/api/slates/active")
    assert response.status_code == 200
    body = response.json()
    assert body["slate"] is not None
    assert body["slate"]["draw_code"] == "PGM-SOON"
    # `seconds_to_close` is the contract the frontend countdown relies on;
    # a few seconds of slack covers per-request overhead.
    assert body["seconds_to_close"] is not None
    assert 6 * 3600 - 10 <= body["seconds_to_close"] <= 6 * 3600 + 10


@pytest.mark.anyio
async def test_active_endpoint_returns_null_slate_when_nothing_open(client) -> None:
    """When the only loaded slate has a past cierre, the endpoint must
    still return 200 with `slate: null`. The frontend depends on this
    contract — it polls every minute and a 204 would noise the console."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    response = await client.post(
        "/api/slates",
        json=_slate_payload(
            draw_code="PG-PAST",
            week_type="midweek",
            closes_at=past,
            kickoff_at=past + timedelta(hours=2),
        ),
    )
    assert response.status_code == 201

    response = await client.get("/api/slates/active")
    assert response.status_code == 200
    body = response.json()
    assert body["slate"] is None
    assert body["seconds_to_close"] is None


@pytest.mark.anyio
async def test_archive_due_slates_flips_is_archived_idempotently(client) -> None:
    """Direct unit-level check on the service: a slate whose cierre has
    passed should flip on the first call and stay archived on the second.
    Idempotency matters because the worker runs the job every 30 seconds
    and a non-idempotent flip would re-emit metrics on every cycle."""
    from app.db.session import SessionLocal
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    future = datetime.now(timezone.utc) + timedelta(hours=6)

    create_responses = []
    for code, closes_at in (("PG-CLOSED", past), ("PG-OPEN", future)):
        response = await client.post(
            "/api/slates",
            json=_slate_payload(
                draw_code=code,
                week_type="weekend",
                closes_at=closes_at,
                kickoff_at=closes_at + timedelta(hours=4),
            ),
        )
        assert response.status_code == 201, response.text
        create_responses.append(response.json())

    session = SessionLocal()
    try:
        service = SlateService(SlateRepository(session))
        archived = service.archive_due_slates()
        assert archived == ["PG-CLOSED"]
        # Second call: nothing new to archive.
        assert service.archive_due_slates() == []
    finally:
        session.close()

    # And the public list now reflects the archive flip — closed slates
    # are filtered out by default in /api/slates.
    response = await client.get("/api/slates")
    draw_codes = {slate["draw_code"] for slate in response.json()}
    assert "PG-CLOSED" not in draw_codes
    assert "PG-OPEN" in draw_codes
