"""R7.0 — Completed-slate learning loop API (strictly read-only).

Exposes the post-jornada learning surface: inventory of completed slates,
per-slate scoring, error attribution, calibration audit and dataset readiness.
Every endpoint is read-only — it computes on demand and writes nothing (no
predictions, no snapshots, no results). Applying official results is a separate,
guarded CLI step.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.db.session import read_only_transaction
from app.repositories.slate_repository import SlateRepository
from app.services.completed_slate_inventory_service import build_completed_slate_inventory
from app.services.learning_calibration_service import build_calibration_audit
from app.services.learning_dataset_readiness_service import build_dataset_readiness
from app.services.learning_error_attribution_service import build_error_attribution
from app.services.learning_slate_scoring_service import (
    LearningSlateScoringService,
    score_comparable_slates,
)
from app.services.slate_service import SlateService

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/completed-slates/inventory", summary="Learning inventory of all slates")
async def completed_slate_inventory(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        return build_completed_slate_inventory(session)


@router.get("/completed-slates/scores", summary="Score all comparable completed slates")
async def completed_slate_scores(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        return score_comparable_slates(session)


@router.get("/slates/{slate_id}/score", summary="Post-jornada score for one slate")
async def slate_score(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        slate = SlateService(SlateRepository(session)).get_slate(slate_id)
        if slate is None:
            raise HTTPException(status_code=404, detail="Slate not found.")
        return LearningSlateScoringService(session).score_slate(slate)


@router.get("/slates/{slate_id}/attribution", summary="Error attribution for one slate")
async def slate_attribution(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        slate = SlateService(SlateRepository(session)).get_slate(slate_id)
        if slate is None:
            raise HTTPException(status_code=404, detail="Slate not found.")
        return build_error_attribution(session, slate)


@router.get("/calibration", summary="Calibration audit over comparable slates")
async def calibration_audit(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        return build_calibration_audit(session)


@router.get("/dataset-readiness", summary="Training/dataset readiness audit")
async def dataset_readiness(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    with read_only_transaction(session):
        return build_dataset_readiness(session)
