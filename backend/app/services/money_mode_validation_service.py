"""R6.0 — Money Mode slate validation (read-only).

Single, pure check of whether an active/upcoming slate is *clean and playable*
for the Money Mode release candidate. It never writes a row: it only reads the
slate composition and counts persisted predictions to classify the slate.

A slate is ``valid_for_money_mode`` when it has no hard ``data_blockers`` — it
exists, is not archived, has matches with contiguous positions and named teams,
and either has persisted predictions or can score them live on demand. Soft
issues (live-only predictions, a closed cierre window) are surfaced as
``warnings`` and never block on their own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models.tables import PredictionModel, ProgolSlateModel
from app.repositories.slate_repository import SlateRepository
from app.services.slate_service import SlateService

# Placeholder substrings that mark an unresolved fixture/team.
_PLACEHOLDER_TOKENS = ("placeholder", "tbd", "por definir", "unknown", "?")


def _persisted_prediction_count(session, slate_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(PredictionModel.id)).where(
                PredictionModel.slate_id == slate_id
            )
        )
        or 0
    )


def _looks_like_placeholder(name: str | None) -> bool:
    if not name or not name.strip():
        return True
    lowered = name.strip().lower()
    return any(token in lowered for token in _PLACEHOLDER_TOKENS)


def validate_slate_for_money_mode(
    session, slate: ProgolSlateModel, now: datetime | None = None
) -> dict[str, Any]:
    """Read-only integrity check for one slate.

    Returns a self-describing dict: ``valid_for_money_mode`` plus the hard
    ``data_blockers`` and soft ``warnings`` that explain the verdict.
    """
    now = now or datetime.now(timezone.utc)
    slate_service = SlateService(SlateRepository(session))

    data_blockers: list[str] = []
    warnings: list[str] = []

    draw_code = slate.draw_code
    match_count = len(slate.matches)

    if slate.is_archived:
        data_blockers.append("slate_archived")

    if match_count == 0:
        data_blockers.append("no_matches")

    # Positions must be contiguous 1..n with no gaps/placeholders.
    positions = sorted(link.position for link in slate.matches)
    if positions and positions != list(range(1, match_count + 1)):
        data_blockers.append("non_contiguous_positions")

    # Team names must be resolved (no placeholders/blanks).
    placeholder_positions = [
        link.position
        for link in slate.matches
        if _looks_like_placeholder(getattr(link.match.home_team, "name", None))
        or _looks_like_placeholder(getattr(link.match.away_team, "name", None))
    ]
    if placeholder_positions:
        data_blockers.append(
            "placeholder_teams_at_" + ",".join(str(p) for p in sorted(placeholder_positions))
        )

    is_closed = slate_service.is_closed(slate, now)
    persisted = _persisted_prediction_count(session, slate.id)
    live_available = bool(match_count) and not slate.is_archived and not is_closed

    if persisted > 0:
        prediction_status = "persisted"
    elif live_available:
        prediction_status = "live_available"
        warnings.append("live_predictions_only")
    elif match_count:
        prediction_status = "pending"
        data_blockers.append("no_predictions_available")
    else:
        prediction_status = "missing"
        data_blockers.append("no_predictions_available")

    if is_closed and not slate.is_archived:
        warnings.append("registration_closed")

    if slate.registration_closes_at is None:
        warnings.append("no_registration_cierre")

    return {
        "draw_code": draw_code,
        "slate_id": slate.id,
        "week_type": slate.week_type,
        "valid_for_money_mode": not data_blockers,
        "data_blockers": data_blockers,
        "warnings": warnings,
        "match_count": match_count,
        "prediction_status": prediction_status,
        "is_archived": bool(slate.is_archived),
        "is_closed": bool(is_closed),
    }
