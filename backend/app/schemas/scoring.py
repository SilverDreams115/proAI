from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConfidenceBandScoreResponse(BaseModel):
    band: str
    hits: int
    total: int
    hit_rate: float | None


class MatchScoreDetailResponse(BaseModel):
    match_id: str
    position: int
    home_team_name: str
    away_team_name: str
    competition_name: str
    result_code: str | None
    home_goals: int | None
    away_goals: int | None
    result_is_canonical: bool = True
    recommended_outcome: str | None
    confidence_band: str | None
    home_probability: float | None
    draw_probability: float | None
    away_probability: float | None
    generated_at: str | None
    hit: bool | None
    brier_score: float | None
    ticket_modes: dict | None


class JornadaScoreResponse(BaseModel):
    id: str
    slate_id: str
    draw_code: str
    week_type: str
    composition_hash: str | None
    slate_version: int | None
    total_matches: int
    matches_with_results: int
    simple_hits: int
    simple_hit_rate: float | None
    ticket_hits: int | None
    ticket_hit_rate: float | None
    brier_score_avg: float | None
    confidence_bands: list[ConfidenceBandScoreResponse]
    details: list[MatchScoreDetailResponse]
    computed_at: datetime
    is_complete: bool
    snapshot_available: bool
