from datetime import datetime

from pydantic import BaseModel


class TeamPayload(BaseModel):
    name: str
    country: str | None = None
    is_placeholder: bool = False


class CompetitionPayload(BaseModel):
    name: str
    country: str | None = None
    season: str | None = None
    is_placeholder: bool = False


class MatchReferencePayload(BaseModel):
    position: int
    competition: CompetitionPayload
    home_team: TeamPayload
    away_team: TeamPayload
    kickoff_at: datetime
    venue: str | None = None
