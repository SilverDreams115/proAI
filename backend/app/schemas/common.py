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
    # Tri-state on purpose, and it must stay that way:
    #   True  -> the caller fabricated this fixture; the kickoff above is a
    #            construction, not an observation.
    #   False -> the caller positively knows a feed reported it.
    #   None  -> the caller has no opinion; an existing row keeps its mark.
    #
    # A plain `False` default would make "this payload does not model the flag"
    # indistinguishable from "a feed confirmed this fixture", and any such
    # payload would silently clear the mark on re-upsert. That is exactly what
    # the current.json round-trip did to all 14 of PG-2344's fabricated rows.
    # See app.services.placeholder_fixtures.
    is_placeholder: bool | None = None
