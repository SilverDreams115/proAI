"""Experimental neural baseline endpoints — NOT production prediction routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.services.adaptive_retraining_service import AdaptiveRetrainingService
from app.services.neural_baseline_service import (
    NeuralBaselineConfig,
    NeuralBaselineRegistryService,
    NeuralBaselineService,
)
from app.services.prediction_service import invalidate_slate_prediction_cache

router = APIRouter(prefix="/training/neural", tags=["training-neural-experimental"])


class NeuralConfigRequest(BaseModel):
    hidden_dims: list[int] = Field(default=[64, 32])
    learning_rate: float = Field(default=0.01, gt=0)
    epochs: int = Field(default=150, ge=1)
    batch_size: int = Field(default=32, ge=1)
    min_rows: int = Field(default=20, ge=1)
    random_seed: int = Field(default=42)
    # "extended" adds match-level features (form, goals, rest, h2h) to the
    # 13 baseline-derived columns. Offline experiment only — the promotion
    # gate refuses an extended artifact because it is not monotone in the
    # baseline and could therefore re-rank a served pick.
    feature_set: Literal["baseline", "extended"] = "baseline"


class NeuralPromoteRequest(BaseModel):
    candidate_run_id: str | None = None
    force: bool = False


def _match_features(session: Session, cfg: NeuralBaselineConfig) -> dict[str, dict[str, Any]]:
    """match_id -> stored feature payload, loaded only for the extended set.

    Reads the persisted `match_feature_snapshots` rather than recomputing:
    the snapshot is what the baseline actually saw for that match at
    prediction time, so training on it keeps the experiment aligned with
    production instead of with today's recomputed view of an old fixture.
    """
    if not cfg.uses_extended_features:
        return {}
    import json

    from app.models.tables import MatchFeatureSnapshotModel
    from sqlalchemy import select

    out: dict[str, dict[str, Any]] = {}
    for snap in session.scalars(select(MatchFeatureSnapshotModel)):
        payload = snap.payload_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            out[snap.match_id] = payload
    return out


def _build_service(config_req: NeuralConfigRequest | None, session: Session) -> NeuralBaselineService:
    cfg = NeuralBaselineConfig(**(config_req.model_dump() if config_req else {}))
    rows = AdaptiveRetrainingService(session)._build_all_rows()
    return NeuralBaselineService(
        rows=rows, config=cfg, match_features=_match_features(session, cfg)
    )


def _build_registry(
    config_req: NeuralConfigRequest | None,
    session: Session,
) -> NeuralBaselineRegistryService:
    from app.repositories.training_repository import TrainingRepository

    cfg = NeuralBaselineConfig(**(config_req.model_dump() if config_req else {}))
    rows = AdaptiveRetrainingService(session)._build_all_rows()
    return NeuralBaselineRegistryService(
        rows=rows,
        training_repository=TrainingRepository(session),
        config=cfg,
        match_features=_match_features(session, cfg),
    )


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


@router.post("/loso", response_model=dict[str, Any])
def post_neural_loso(
    config: NeuralConfigRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Leave-one-slate-out evaluation. Trains per fold, persists nothing.

    One training run per slate, so it is slower than /dry-run — that is the
    price of a verdict that does not hinge on which single jornada happened
    to land in the holdout.
    """
    return _build_service(config, session).walk_forward_loso()


@router.post("/candidates/train", response_model=dict[str, Any])
def post_neural_train_candidate(
    config: NeuralConfigRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Train and save a non-production neural candidate artifact."""
    registry = _build_registry(config, session)
    result = registry.train_candidate()
    if result.get("saved"):
        session.commit()
    return result


@router.get("/candidates/latest", response_model=dict[str, Any])
def get_latest_neural_candidate(
    include_artifact: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return the latest saved non-production candidate."""
    return _build_registry(None, session).latest_candidate(include_artifact=include_artifact)


@router.get("/active", response_model=dict[str, Any])
def get_active_neural_model(
    include_artifact: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return the latest promoted neural baseline, if any."""
    return _build_registry(None, session).active(include_artifact=include_artifact)


@router.post("/promote", response_model=dict[str, Any])
def post_promote_neural_candidate(
    payload: NeuralPromoteRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Promote a saved candidate to the active neural baseline slot.

    The promotion gate requires Brier improvement unless ``force=true``.
    This does not affect production predictions.
    """
    from app.services.learning_dataset_readiness_service import build_dataset_readiness

    req = payload or NeuralPromoteRequest()
    dataset_gate = build_dataset_readiness(session)
    if not dataset_gate.get("training_ready"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Neural promotion blocked: learning dataset gate is not ready.",
                "reason": dataset_gate.get("reason"),
                "minimum_missing": dataset_gate.get("minimum_missing") or [],
                "recommended_action": dataset_gate.get("recommended_next_data_action"),
            },
        )
    result = _build_registry(None, session).promote_candidate(
        candidate_run_id=req.candidate_run_id,
        force=req.force,
    )
    if result.get("promoted"):
        session.commit()
        invalidate_slate_prediction_cache(None)
    return result


@router.post("/rollback", response_model=dict[str, Any])
def post_rollback_neural_active(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Rollback active neural baseline to the previous active run."""
    result = _build_registry(None, session).rollback_active()
    if result.get("rolled_back"):
        session.commit()
        invalidate_slate_prediction_cache(None)
    return result
