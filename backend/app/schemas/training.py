from datetime import datetime

from pydantic import BaseModel
from pydantic import Field


class TrainModelRequest(BaseModel):
    model_name: str = "elo_poisson_blend"


class EvaluateModelRequest(BaseModel):
    model_name: str = "elo_poisson_blend"
    min_training_matches: int = Field(default=6, ge=1)
    confidence_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)


class TrainingEvaluationResponse(BaseModel):
    model_name: str
    evaluation_mode: str
    matches_considered: int
    matches_evaluated: int
    min_training_matches: int
    confidence_threshold: float
    hit_rate: float
    brier_score: float
    log_loss: float
    confident_pick_rate: float
    confident_pick_hit_rate: float
    ready_for_live_picks: bool
    verdict: str
    thresholds: dict[str, float | int]


class CompetitionTrainingEvaluationResponse(TrainingEvaluationResponse):
    competition_key: str
    competition_name: str


class CompetitionEvaluationReportResponse(BaseModel):
    model_name: str
    evaluation_mode: str
    min_training_matches: int
    confidence_threshold: float
    competitions_considered: int
    competitions_ready: int
    competitions: list[CompetitionTrainingEvaluationResponse]


class CalibrationBinResponse(BaseModel):
    confidence_bin: str
    matches: int
    average_confidence: float
    hit_rate: float
    calibration_gap: float
    brier_score: float


class CalibrationReportResponse(BaseModel):
    model_name: str
    evaluation_mode: str
    matches_considered: int
    matches_evaluated: int
    min_training_matches: int
    confidence_threshold: float
    accepted_picks: int
    accepted_pick_rate: float
    bins: list[CalibrationBinResponse]


class TrainingRunResponse(BaseModel):
    id: str
    model_name: str
    trained_at: datetime
    training_sample_size: int
    artifact: dict[str, object]
