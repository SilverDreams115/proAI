"""DB-aware Date Sanity Gate wrapper.

Gathers the inputs the pure ``evaluate_slate_dates`` needs (previous
same-week_type cierre, the source's extraction confidence + observed_at from
the proposal) and returns the status for a slate. Read-only.
"""
from __future__ import annotations

import json
import re

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


def _proposal_meta(session: Session, slate: ProgolSlateModel) -> dict[str, object]:
    """Extraction metadata from the latest PDF proposal for this draw_code:
    extraction_confidence, observed_at, and whether the PDF carried valid
    fixtures / a rejected (wrong-concurso) cierre block."""
    digits = str(_trailing_int(slate.draw_code) or "")
    proposal = session.scalar(
        select(ProgolSlateProposalModel)
        .where(
            ProgolSlateProposalModel.draw_code == digits,
            ProgolSlateProposalModel.source_name != "operator_date_override",
        )
        .order_by(ProgolSlateProposalModel.last_seen_at.desc())
        .limit(1)
    )
    if proposal is None:
        return {}
    meta: dict[str, object] = {"observed_at": proposal.last_seen_at}
    try:
        payload = json.loads(proposal.payload_json or "{}")
    except (ValueError, TypeError):
        return meta
    meta["extraction_confidence"] = payload.get("extraction_confidence")
    meta["registration_close_source"] = payload.get("registration_close_source")
    block = payload.get("block_diagnostics") or {}
    meta["rejected_close_block"] = bool(block.get("rejected_close_block_draw_code"))
    fixtures = payload.get("fixtures") or []
    meta["fixtures_present"] = bool(fixtures) or bool(payload.get("match_count"))
    return meta


def slate_date_status(session: Session, slate: ProgolSlateModel) -> tuple[DateStatus, list[str]]:
    kickoffs = [sm.match.kickoff_at for sm in slate.matches if sm.match is not None]
    meta = _proposal_meta(session, slate)
    return evaluate_slate_dates(
        registration_closes_at=slate.registration_closes_at,
        kickoffs=kickoffs,
        created_at=slate.created_at,
        observed_at=meta.get("observed_at"),  # type: ignore[arg-type]
        prev_same_type_closes_at=_prev_same_type_closes_at(session, slate),
        extraction_confidence=meta.get("extraction_confidence"),  # type: ignore[arg-type]
        registration_close_source=meta.get("registration_close_source"),  # type: ignore[arg-type]
        fixtures_present=bool(meta.get("fixtures_present")),
        rejected_close_block=bool(meta.get("rejected_close_block")),
    )
