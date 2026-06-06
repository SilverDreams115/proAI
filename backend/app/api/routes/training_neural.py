"""Experimental neural baseline endpoints — NOT production prediction routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.services.adaptive_retraining_service import AdaptiveRetrainingService
from app.services.neural_baseline_service import (
    NeuralBaselineConfig,
    NeuralBaselineService,
)

router = APIRouter(prefix="/training/neural", tags=["training-neural-experimental"])


class NeuralConfigRequest(BaseModel):
    hidden_dims: list[int] = Field(default=[64, 32])
    learning_rate: float = Field(default=0.01, gt=0)
    epochs: int = Field(default=150, ge=1)
    batch_size: int = Field(default=32, ge=1)
    min_rows: int = Field(default=20, ge=1)
    random_seed: int = Field(default=42)


def _build_service(config_req: NeuralConfigRequest | None, session: Session) -> NeuralBaselineService:
    cfg = NeuralBaselineConfig(**(config_req.model_dump() if config_req else {}))
    rows = AdaptiveRetrainingService(session)._build_all_rows()
    return NeuralBaselineService(rows=rows, config=cfg)


@router.get("/readiness", response_model=dict[str, Any])
def get_neural_readiness(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return dataset readiness for neural baseline training.

    Never trains, never saves anything — pure read.
    """
    svc = _build_service(None, session)
    return svc.readiness()


@router.post("/dry-run", response_model=dict[str, Any])
def post_neural_dry_run(
    config: NeuralConfigRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Run training on current data without saving any artifact.

    Returns training metrics if enough rows exist, otherwise explains
    why training was skipped.

    NOT a production endpoint — the result is not persisted.
    """
    svc = _build_service(config, session)
    return svc.dry_run_train()
