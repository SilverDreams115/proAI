"""Adaptive retraining gate endpoints.

GET  /api/training/adaptive/readiness  — evaluate readiness (no DB change)
POST /api/training/adaptive/dry-run    — same as readiness, explicit label
POST /api/training/adaptive/run        — execute retrain if gates pass
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.schemas.adaptive_retraining import (
    DryRunReport,
    ReadinessReport,
    RetrainingResult,
    RetrainingThresholds,
)
from app.services.adaptive_retraining_service import AdaptiveRetrainingService

router = APIRouter(prefix="/training/adaptive", tags=["training-adaptive"])


@router.get("/readiness", response_model=ReadinessReport)
def get_adaptive_readiness(
    session: Session = Depends(get_db_session),
) -> ReadinessReport:
    """Inspect the adaptive dataset and return a readiness verdict.

    Does not modify any DB state.
    """
    return AdaptiveRetrainingService(session).evaluate_readiness()


@router.post("/dry-run", response_model=DryRunReport)
def post_adaptive_dry_run(
    thresholds: RetrainingThresholds | None = None,
    session: Session = Depends(get_db_session),
) -> DryRunReport:
    """Return a readiness verdict with optional custom thresholds.

    Does not modify any DB state.
    """
    return AdaptiveRetrainingService(session).dry_run(thresholds)


@router.post("/run", response_model=RetrainingResult, status_code=200)
def post_adaptive_run(
    thresholds: RetrainingThresholds | None = None,
    session: Session = Depends(get_db_session),
) -> RetrainingResult:
    """Execute the retrain pipeline when readiness gates pass.

    Returns 409 when not ready so the caller can inspect the reasons
    without relying on exception handling.
    """
    result = AdaptiveRetrainingService(session).run_retraining_if_ready(thresholds)
    if not result.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Retraining blocked: readiness gates not met.",
                "recommended_action": result.recommended_action,
                "reasons": result.reasons,
            },
        )
    return result
