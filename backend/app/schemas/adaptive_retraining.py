"""Schemas for adaptive retraining gate and dry-run endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RetrainingThresholds(BaseModel):
    min_trainable_rows: int = Field(default=50, ge=1)
    min_complete_slates: int = Field(default=3, ge=1)
    max_conflict_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    max_blocked_rate_for_full_retrain: float = Field(default=0.60, ge=0.0, le=1.0)
    min_new_rows_since_last_train: int = Field(default=30, ge=0)


class ReadinessCheck(BaseModel):
    name: str
    passed: bool
    value: float | int | str | None
    threshold: float | int | str | None
    reason: str


class BandComparison(BaseModel):
    band: str
    hits_before: int
    total_before: int
    hit_rate_before: float | None
    hits_after: int
    total_after: int
    hit_rate_after: float | None
    brier_before: float | None
    brier_after: float | None


class WeekTypeComparison(BaseModel):
    week_type: str
    hits_before: int
    total_before: int
    hit_rate_before: float | None
    hits_after: int
    total_after: int
    hit_rate_after: float | None
    brier_before: float | None
    brier_after: float | None


class ModelComparison(BaseModel):
    rows_evaluated: int
    brier_score_before: float | None
    brier_score_after: float | None
    brier_delta: float | None
    hit_rate_before: float | None
    hit_rate_after: float | None
    hit_rate_delta: float | None
    improved: bool
    by_confidence_band: list[BandComparison]
    by_week_type: list[WeekTypeComparison]


class ReadinessReport(BaseModel):
    ready: bool
    recommended_action: str  # skip | recalibrate_only | confidence_band_adjustment | full_xgboost_retrain
    trainable_rows: int
    complete_slates: int
    blocked_rate: float | None
    conflict_rate: float | None
    new_rows_since_last_train: int
    last_training_run_id: str | None
    last_training_run_at: datetime | None
    checks: list[ReadinessCheck]
    thresholds: RetrainingThresholds


class DryRunReport(ReadinessReport):
    """Identical to ReadinessReport; returned by POST /adaptive/dry-run.

    Named separately so callers can distinguish the intent — no DB changes occur.
    """


class RetrainingResult(BaseModel):
    success: bool
    ready: bool
    recommended_action: str
    reasons: list[str]
    training_run_id: str | None
    rollback_run_id: str | None
    comparison: ModelComparison | None
