from datetime import datetime

from pydantic import BaseModel


class TeamStatResponse(BaseModel):
    id: str
    team_id: str
    source_id: str
    captured_at: datetime
    stat_type: str
    value: float
    sample_size: int


class MatchStatResponse(BaseModel):
    id: str
    match_id: str
    source_id: str
    captured_at: datetime
    stat_type: str
    home_value: float
    away_value: float
