"""R6.4 — Tracking / completed-slate results validation (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.slate_repository import SlateRepository
from app.services.operational_prediction_audit_service import OperationalPredictionAuditService

router = APIRouter(prefix="/tracking", tags=["tracking"])


@router.get("/completed-slates/results-validation")
async def completed_slates_results_validation(
    session: Session = Depends(get_db_session),
) -> dict:
    """R6.4 read-only result-validation dry-run for every completed slate.

    Compares predictions vs local/provider results, computes coverage and flags
    blockers — without writing any match_results.
    """
    from app.db.session import read_only_transaction
    from app.services.completed_slate_results_validation_service import (
        build_completed_slates_validation,
    )

    with read_only_transaction(session):
        return build_completed_slates_validation(session)


@router.get("/operational-prediction-audit")
async def operational_prediction_audit(
    slate_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> dict:
    """Read-only operational audit: prediction results, placeholders,
    explainable confidence, publish gate and live-result freshness."""
    if slate_id is not None and SlateRepository(session).get_slate(slate_id) is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return OperationalPredictionAuditService(session).build(slate_id=slate_id)


@router.get("/slates/{slate_id}/results-validation")
async def slate_results_validation(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    """R6.4 read-only result-validation dry-run for one slate."""
    from app.db.session import read_only_transaction
    from app.services.completed_slate_results_validation_service import (
        build_completed_slate_validation,
    )

    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    with read_only_transaction(session):
        return build_completed_slate_validation(session, slate)
