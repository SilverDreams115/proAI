from datetime import datetime

from pydantic import BaseModel, Field


class PlayerAvailabilityResponse(BaseModel):
    id: str
    match_id: str
    team_id: str
    team_name: str | None = None
    player_id: str | None
    source_id: str
    source_name: str | None = None
    source_url: str | None = None
    evidence_id: str | None
    captured_at: datetime
    status: str
    category: str
    player_name: str
    detail: str
    confidence: float = Field(ge=0.0, le=1.0)
    impact_score: float = Field(ge=0.0, le=1.0)
