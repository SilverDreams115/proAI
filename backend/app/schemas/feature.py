from datetime import datetime

from pydantic import BaseModel


class MatchFeatureResponse(BaseModel):
    match_id: str
    generated_at: datetime
    feature_set_version: str
    home_team_name: str
    away_team_name: str
    competition_name: str
    payload: dict[str, object]


class MatchDataQualityResponse(BaseModel):
    match_id: str
    position: int
    home_team_name: str
    away_team_name: str
    competition_name: str
    quality_score: int
    quality_level: str
    evidence_count: int
    recent_results_count: int
    head_to_head_results_count: int
    availability_count: int
    competition_readiness: str
    live_pick_allowed: bool
    missing: list[str]
    notes: list[str]
