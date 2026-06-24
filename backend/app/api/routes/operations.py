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


@router.get("/dashboard-fast")
async def get_dashboard_fast(
    session: Session = Depends(get_db_session),
) -> dict:
    """R6.3 lightweight active-slate summary for fast first paint.

    Returns the active/upcoming slates + a default selection + cheap validation
    statuses WITHOUT computing Money Mode. Strictly read-only.
    """
    from app.db.session import read_only_transaction
    from app.services.money_mode_operations_service import build_dashboard_fast

    with read_only_transaction(session):
        return build_dashboard_fast(session)
