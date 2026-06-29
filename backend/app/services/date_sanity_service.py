"""DB-aware Date Sanity Gate wrapper.

Gathers the inputs the pure ``evaluate_slate_dates`` needs (previous
same-week_type cierre, the source's extraction confidence + observed_at from
the proposal) and returns the status for a slate. Read-only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel, ProgolSlateProposalModel
from app.services.date_sanity import DateStatus, evaluate_slate_dates


def _trailing_int(draw_code: str) -> int | None:
    m = re.search(r"(\d+)$", draw_code or "")
    return int(m.group(1)) if m else None


def _prev_same_type_closes_at(session: Session, slate: ProgolSlateModel):
    """registration_closes_at of the immediately-lower draw_code of the same
    week_type (numeric trailing digits)."""
    current = _trailing_int(slate.draw_code)
    if current is None:
        return None
    best_n = None
    best_closes = None
    for other in session.scalars(
        select(ProgolSlateModel).where(
            ProgolSlateModel.week_type == slate.week_type,
            ProgolSlateModel.id != slate.id,
        )
    ):
        n = _trailing_int(other.draw_code)
        if n is None or n >= current:
            continue
        if best_n is None or n > best_n:
            best_n = n
            best_closes = other.registration_closes_at
    return best_closes


def _proposal_meta(
    session: Session, slate: ProgolSlateModel
) -> tuple[str | None, "datetime | None"]:
    """(extraction_confidence, observed_at) from the latest proposal for this
    draw_code's trailing digits, if any."""
    digits = str(_trailing_int(slate.draw_code) or "")
    proposal = session.scalar(
        select(ProgolSlateProposalModel)
        .where(ProgolSlateProposalModel.draw_code == digits)
        .order_by(ProgolSlateProposalModel.last_seen_at.desc())
        .limit(1)
    )
    if proposal is None:
        return None, None
    confidence = None
    try:
        payload = json.loads(proposal.payload_json or "{}")
        confidence = payload.get("extraction_confidence")
    except (ValueError, TypeError):
        confidence = None
    return confidence, proposal.last_seen_at


def slate_date_status(session: Session, slate: ProgolSlateModel) -> tuple[DateStatus, list[str]]:
    kickoffs = [sm.match.kickoff_at for sm in slate.matches if sm.match is not None]
    extraction_confidence, observed_at = _proposal_meta(session, slate)
    return evaluate_slate_dates(
        registration_closes_at=slate.registration_closes_at,
        kickoffs=kickoffs,
        created_at=slate.created_at,
        observed_at=observed_at,
        prev_same_type_closes_at=_prev_same_type_closes_at(session, slate),
        extraction_confidence=extraction_confidence,
    )
