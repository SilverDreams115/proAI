from datetime import datetime

from pydantic import BaseModel
from pydantic import Field

from app.schemas.common import MatchReferencePayload


class ProgolSlateCreate(BaseModel):
    label: str
    draw_code: str
    week_type: str = Field(pattern="^(midweek|weekend|revancha)$")
    registration_closes_at: datetime | None = None
    is_archived: bool = False
    matches: list[MatchReferencePayload] = Field(min_length=1, max_length=14)


class SlateMatchResponse(BaseModel):
    position: int
    match_id: str
    competition_name: str
    home_team_name: str
    away_team_name: str
    kickoff_at: datetime
    venue: str | None = None


class ProgolSlateResponse(BaseModel):
    id: str
    label: str
    draw_code: str
    week_type: str
    registration_closes_at: datetime | None = None
    is_archived: bool = False
    is_closed: bool = False
    created_at: datetime
    matches: list[SlateMatchResponse]


class ActiveSlateResponse(BaseModel):
    # Returned by GET /api/slates/active. `slate` is null when no slate is
    # currently active (all archived or cierre passed and the next one
    # hasn't been loaded). `server_time` lets the frontend correct for
    # client-side clock drift when computing the countdown.
    slate: ProgolSlateResponse | None = None
    seconds_to_close: int | None = None
    server_time: datetime


class SlateProposalFixture(BaseModel):
    position: int
    home: str
    away: str


class SlateProposalResponse(BaseModel):
    id: str
    draw_code: str
    week_type: str
    source_name: str
    source_url: str
    registration_closes_at: datetime | None = None
    status: str  # observed | validated | promoted
    observations: int
    first_seen_at: datetime
    last_seen_at: datetime
    fixtures: list[SlateProposalFixture]
    promoted_slate_id: str | None = None
