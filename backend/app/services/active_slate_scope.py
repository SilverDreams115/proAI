"""Active/upcoming slate scope (R5.6-D).

Single source of truth for "which slates are active/upcoming right now". Used
to generalize served behaviour (canary scope, multi-slate UI) so it operates by
rule on every active/upcoming slate — weekend, midweek/MS and any future one —
instead of being hardcoded to a single draw_code.

A slate is active/upcoming when it is not archived and its registration cierre
has not passed (mirrors ``SlateService.is_closed``). Read-only: no DB writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.repositories.slate_repository import SlateRepository
from app.services.slate_service import SlateService


@dataclass(frozen=True)
class ActiveSlateInfo:
    slate_id: str
    draw_code: str
    week_type: str
    match_count: int
    is_archived: bool
    registration_closes_at: datetime | None
    status: str  # "active_upcoming"


def build_active_slate_scope(
    session, now: datetime | None = None
) -> list[ActiveSlateInfo]:
    """Return every active/upcoming slate, soonest cierre first.

    Reuses ``SlateService`` so the active/closed rule lives in one place; a
    future slate enters automatically once it exists, is not archived and its
    cierre is in the future.
    """
    now = now or datetime.now(timezone.utc)
    service = SlateService(SlateRepository(session))
    # list_slates(include_closed=False) already filters out archived/closed and
    # sorts open slates first by urgency.
    active = service.list_slates(include_closed=False)
    return [
        ActiveSlateInfo(
            slate_id=slate.id,
            draw_code=slate.draw_code,
            week_type=slate.week_type,
            match_count=len(slate.matches),
            is_archived=bool(slate.is_archived),
            registration_closes_at=slate.registration_closes_at,
            status="active_upcoming",
        )
        for slate in active
    ]


def is_slate_active_upcoming(session, slate, now: datetime | None = None) -> bool:
    """True when this specific slate is active/upcoming (not archived/closed)."""
    now = now or datetime.now(timezone.utc)
    service = SlateService(SlateRepository(session))
    return not service.is_closed(slate, now)
