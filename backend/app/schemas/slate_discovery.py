from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import CompetitionPayload
from app.schemas.common import TeamPayload


class SlateDiscoveryRequest(BaseModel):
    label: str | None = None
    draw_code: str | None = None
    week_type: str | None = Field(default=None, pattern="^(midweek|weekend|revancha)$")
    registration_closes_at: datetime | None = None
    catalog_source_id: str | None = None
    fixture_source_ids: list[str] = Field(default_factory=list)
    kickoff_not_before: datetime | None = None
    kickoff_not_after: datetime | None = None
    create_persisted_slate: bool = True


class DiscoveredSlateMatchResponse(BaseModel):
    position: int
    competition: CompetitionPayload
    home_team: TeamPayload
    away_team: TeamPayload
    kickoff_at: datetime
    venue: str | None = None
    source_document_id: str | None = None
    source_name: str | None = None


class SlateDiscoveryResponse(BaseModel):
    label: str
    draw_code: str
    week_type: str
    registration_closes_at: datetime | None = None
    match_target: int
    source_catalog_title: str | None = None
    source_catalog_url: str | None = None
    persisted_slate_id: str | None = None
    matches: list[DiscoveredSlateMatchResponse]
