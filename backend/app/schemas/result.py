from datetime import datetime

from pydantic import BaseModel


class MatchResultResponse(BaseModel):
    id: str
    match_id: str
    source_id: str
    played_at: datetime
    home_goals: int
    away_goals: int
    result_code: str


class MatchContextResultResponse(MatchResultResponse):
    home_team_name: str
    away_team_name: str
    competition_name: str
    is_head_to_head: bool = False
    context_label: str = "Forma reciente"
