"""R6.1 — Operational Money Mode status (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/money-mode/status")
async def get_money_mode_operational_status(
    session: Session = Depends(get_db_session),
) -> dict:
    """Compact operational status for every active/upcoming slate.

    For each active/upcoming slate it returns the Money Mode play/don't-play
    decision plus whether the slate is Money-Mode-ready. Strictly read-only: the
    transaction is marked READ ONLY and rolled back, so it never writes a
    prediction, ticket snapshot or feature snapshot and never touches the real
    ticket.
    """
    from app.db.session import read_only_transaction
    from app.services.money_mode_operations_service import build_operational_status

    with read_only_transaction(session):
        return build_operational_status(session)
