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
    has_predictions: bool = False
    has_valid_snapshot: bool = False
    status_label: str = "Sin predicción"
    # R5.6 hotfix: distinguish persisted vs live-available vs pending so the UI
    # never shows a false "Sin predicción" for an active slate whose predictions
    # can be served live (read-only) on demand.
    prediction_status: str = "missing"  # persisted|live_available|pending|missing
    persisted_prediction_count: int = 0
    match_count: int = 0
    live_prediction_available: bool = False


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
    # True when a non-archived slate already exists for this draw_code with
    # the same composition — the UI should show "Ya activa / Ver boleta"
    # instead of the "Usar esta boleta" promote button.
    is_already_active: bool = False
    active_slate_id: str | None = None


class PromoteProposalResponse(BaseModel):
    """Returned by POST /slates/proposed/{id}/promote.

    already_active=True means a slate for this draw_code was already
    active with the same fixture composition — no new slate was created.
    already_active=False means a fresh slate (or updated slate) was promoted.
    """
    already_active: bool
    slate: ProgolSlateResponse
