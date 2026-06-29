from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LiveDrawRisk(BaseModel):
    p_draw: float
    draw_rank: int
    is_live_draw: bool
    is_strong_draw: bool
    covered_simple: bool
    covered_doubles: bool
    covered_full: bool


class LiveTicketMode(BaseModel):
    pick_type: str | None = None
    picks: list[str] = []
    hit: bool | None = None


class LiveMatchResult(BaseModel):
    match_id: str
    position: int
    home_team_name: str
    away_team_name: str
    competition_name: str
    kickoff_at: datetime | None = None
    predicted_outcome: str | None = None
    confidence_band: str | None = None
    home_probability: float | None = None
    draw_probability: float | None = None
    away_probability: float | None = None
    # Raw model output (pre-guardrail). Surfaced for transparency only —
    # the *_probability fields above are the calibrated/visible vector.
    raw_probabilities: dict[str, float] | None = None
    # Conservative draw (X) calibration trace.
    draw_calibration_applied: bool = False
    draw_calibration_reason: str | None = None
    pre_draw_calibration_probabilities: dict[str, float] | None = None
    home_goals: int | None = None
    away_goals: int | None = None
    result_code: str | None = None
    minute: int | None = None
    status: str
    is_final: bool
    is_live: bool
    is_pending: bool
    source: str | None = None
    source_updated_at: datetime | None = None
    prediction_hit: bool | None = None
    simple_hit: bool | None = None
    doubles_hit: bool | None = None
    full_hit: bool | None = None
    ticket_modes: dict[str, LiveTicketMode] | None = None
    draw_was_real: bool | None = None
    draw_was_covered: bool = False
    draw_risk: LiveDrawRisk | None = None


class LiveResultsResponse(BaseModel):
    slate_id: str
    draw_code: str
    week_type: str
    is_archived: bool
    composition_hash: str | None = None
    match_count: int
    completed_count: int
    live_count: int
    pending_count: int
    is_complete: bool
    last_updated_at: datetime | None = None
    matches: list[LiveMatchResult]


class LiveScoreResponse(BaseModel):
    slate_id: str
    draw_code: str
    week_type: str
    total_matches: int
    evaluated_matches: int
    live_matches: int
    pending_matches: int
    simple_hits: int
    doubles_hits: int
    full_hits: int
    simple_possible_remaining: int
    doubles_possible_remaining: int
    full_possible_remaining: int
    current_hit_rate: float | None = None
    max_possible_hits: int
    min_possible_hits: int
    empates_reales_hasta_ahora: int
    empates_esperados: float
    empates_esperados_evaluados: float
    draw_delta_partial: float
    brier_partial: float | None = None
    is_complete: bool
    last_updated_at: datetime | None = None


class LiveDashboardEntry(BaseModel):
    slate_id: str
    draw_code: str
    week_type: str
    is_archived: bool
    is_closed: bool
    classification: str = "unverified"
    comparable: bool = False
    status_label: str
    match_count: int
    completed_count: int
    live_count: int
    pending_count: int
    simple_hits: int
    doubles_hits: int
    full_hits: int
    empates_reales: int
    empates_esperados: float
    max_possible_hits: int
    current_hit_rate: float | None = None
    is_complete: bool
    last_updated_at: datetime | None = None


class LiveDashboardResponse(BaseModel):
    closed: list[LiveDashboardEntry]
    open: list[LiveDashboardEntry]


class OriginalSnapshotMeta(BaseModel):
    snapshot_id: str | None = None
    generated_at: datetime | None = None
    composition_hash: str | None = None
    model_version: str | None = None


class ComparisonMatch(LiveMatchResult):
    diagnosis: str


class ResultComparisonResponse(BaseModel):
    slate_id: str
    draw_code: str
    week_type: str
    is_archived: bool
    composition_hash: str | None = None
    classification: str = "unverified"
    comparable: bool = False
    classification_reasons: list[str] = []
    competitions: list[str] = []
    source_name: str | None = None
    source_url: str | None = None
    match_count: int
    completed_count: int
    live_count: int
    pending_count: int
    is_complete: bool
    results_ingested: bool
    last_updated_at: datetime | None = None
    original_snapshot: OriginalSnapshotMeta
    score: LiveScoreResponse
    matches: list[ComparisonMatch]
