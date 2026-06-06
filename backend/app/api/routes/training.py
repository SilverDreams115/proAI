import json

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.entity_repository import EntityRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.training_repository import TrainingRepository
from app.schemas.training import TrainModelRequest
from app.schemas.training import CalibrationReportResponse
from app.schemas.training import CompetitionEvaluationReportResponse
from app.schemas.training import EvaluateModelRequest
from app.schemas.training import TrainingEvaluationResponse
from app.schemas.training import TrainingRunResponse
from app.services.model_training_service import ModelTrainingService

router = APIRouter(prefix="/training", tags=["training"])


@router.post("/models/train", response_model=TrainingRunResponse, status_code=201)
async def train_models(
    payload: TrainModelRequest,
    session: Session = Depends(get_db_session),
) -> TrainingRunResponse:
    service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    service.train(payload.model_name)
    latest = TrainingRepository(session).latest_run(payload.model_name)
    assert latest is not None
    return TrainingRunResponse(
        id=latest.id,
        model_name=latest.model_name,
        trained_at=latest.trained_at,
        training_sample_size=latest.training_sample_size,
        artifact=json.loads(latest.artifact_json),
    )


@router.post("/models/evaluate", response_model=TrainingEvaluationResponse)
async def evaluate_model(
    payload: EvaluateModelRequest,
    session: Session = Depends(get_db_session),
) -> TrainingEvaluationResponse:
    service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    evaluation = service.evaluate_walk_forward(
        model_name=payload.model_name,
        min_training_matches=payload.min_training_matches,
        confidence_threshold=payload.confidence_threshold,
    )
    return TrainingEvaluationResponse.model_validate(evaluation)


@router.post("/models/evaluate/competitions", response_model=CompetitionEvaluationReportResponse)
async def evaluate_model_by_competition(
    payload: EvaluateModelRequest,
    session: Session = Depends(get_db_session),
) -> CompetitionEvaluationReportResponse:
    service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    evaluation = service.evaluate_competitions_walk_forward(
        model_name=payload.model_name,
        min_training_matches=payload.min_training_matches,
        confidence_threshold=payload.confidence_threshold,
    )
    return CompetitionEvaluationReportResponse.model_validate(evaluation)


@router.post("/models/evaluate/calibration", response_model=CalibrationReportResponse)
async def evaluate_model_calibration(
    payload: EvaluateModelRequest,
    session: Session = Depends(get_db_session),
) -> CalibrationReportResponse:
    service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    report = service.calibration_report(
        model_name=payload.model_name,
        min_training_matches=payload.min_training_matches,
        confidence_threshold=payload.confidence_threshold,
    )
    return CalibrationReportResponse.model_validate(report)


@router.get("/models/drift")
async def drift_report(
    model_name: str | None = None,
    sample_size: int = 200,
    session: Session = Depends(get_db_session),
) -> dict:
    service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    return service.drift_report(model_name=model_name, sample_size=sample_size)


@router.get("/backtest/history")
async def backtest_history(
    competition_key: str | None = None,
    limit: int = 50,
) -> dict:
    """Return the published walk-forward trail from `reports/backtest_history/`.

    Loads files written by `make publish-backtest`. Filters by competition
    when provided and trims each competition's entries to the most recent
    `limit` matches so the response stays bounded."""
    import json as _json
    from pathlib import Path

    # See cli.publish-backtest: the canonical location moved to the
    # /data volume so the verdict persists across container rebuilds.
    # Fall back to the legacy reports/ path so older deployments
    # still serve a response while the operator migrates.
    root = Path("/data") / "backtest_history"
    if not root.is_dir():
        root = Path("reports") / "backtest_history"
    if not root.is_dir():
        return {"available": False, "reason": "no_published_history", "competitions": []}
    index_path = root / "index.json"
    if not index_path.is_file():
        return {"available": False, "reason": "missing_index", "competitions": []}
    index = _json.loads(index_path.read_text(encoding="utf-8"))
    payloads: list[dict] = []
    for entry in index.get("competitions", []):
        key = entry.get("competition_key")
        if competition_key and key != competition_key:
            continue
        file_name = entry.get("file")
        if not isinstance(file_name, str):
            continue
        file_path = root / file_name
        if not file_path.is_file():
            continue
        data = _json.loads(file_path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        if isinstance(entries, list) and limit > 0:
            entries = entries[-limit:]
        data["entries"] = entries
        payloads.append(data)
    return {
        "available": True,
        "generated_at": index.get("generated_at"),
        "model_name": index.get("model_name"),
        "competitions": payloads,
    }
