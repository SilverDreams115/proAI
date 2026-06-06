from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.schemas.adaptive_dataset import AdaptiveDatasetRow, AdaptiveDatasetSummary
from app.services.adaptive_dataset_service import AdaptiveDatasetService

router = APIRouter(prefix="/adaptive-dataset", tags=["adaptive-dataset"])


@router.get("/summary", response_model=AdaptiveDatasetSummary)
def get_adaptive_dataset_summary(
    include_partial: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> AdaptiveDatasetSummary:
    return AdaptiveDatasetService(session).build_summary(include_partial=include_partial)


@router.get("/slates/{slate_id}", response_model=list[AdaptiveDatasetRow])
def get_adaptive_dataset_for_slate(
    slate_id: str,
    include_partial: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> list[AdaptiveDatasetRow]:
    rows = AdaptiveDatasetService(session).build_rows_for_slate(
        slate_id, include_partial=include_partial
    )
    if not rows and not include_partial:
        # Surface 404 when slate has no scored data rather than a silent empty list.
        # The caller can retry with ?include_partial=true for in-progress jornadas.
        from app.models.tables import ProgolJornadaScoreModel
        from sqlalchemy import select

        existing = session.scalar(
            select(ProgolJornadaScoreModel).where(
                ProgolJornadaScoreModel.slate_id == slate_id
            )
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="No jornada score found for this slate.")
    return rows
