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
    # Official-lineage classification (additive; defaults keep older callers
    # working). `read_only` is true for closed/archived slates so the UI
    # disables generate/reset and shows the postmortem instead.
    classification: str | None = None
    comparable: bool = False
    has_results: bool = False
    read_only: bool = False
    # Date Sanity Gate: date_valid | date_suspect | stale_source | parse_error
    # | needs_operator_confirmation. A non-valid slate is never shown as open.
    date_status: str = "date_valid"
    date_suspect: bool = False
    date_status_reasons: list[str] = []


class DiscoveryInfo(BaseModel):
    """Discovery / worker heartbeat shown in the empty state so the operator
    sees WHY there is no open slate, not a blank screen."""

    last_weekend_draw_code: str | None = None
    last_weekend_status: str | None = None
    last_weekend_seen_at: datetime | None = None
    last_midweek_draw_code: str | None = None
    last_midweek_status: str | None = None
    last_midweek_seen_at: datetime | None = None
    last_observed_at: datetime | None = None
    # Official slates held back by the Date Sanity Gate (stale/suspect dates),
    # surfaced so the empty/diagnostics view explains why they aren't open.
    suspect_slates: list[dict] = []
    # MS PDF watcher diagnostics (observe_progol_ms_pdf).
    last_ms_pdf_checked_at: str | None = None
    last_ms_pdf_sha256: str | None = None
    last_ms_pdf_changed_at: str | None = None
    # unchanged | changed_valid | changed_invalid | parse_error
    last_ms_pdf_status: str | None = None
    current_ms_candidate: dict | None = None
    ms_pdf_recommended_action: str | None = None


class VisibleSlatesResponse(BaseModel):
    """Selector source of truth: open official slates first, else the most
    recent official slates in read-only mode, so the UI is never empty."""

    open_slates: list[ProgolSlateResponse] = []
    recent_slates: list[ProgolSlateResponse] = []
    selected_default_slate_id: str | None = None
    # open_slate | fallback_recent | no_official_slates
    reason: str = "no_official_slates"
    discovery: DiscoveryInfo = DiscoveryInfo()


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
