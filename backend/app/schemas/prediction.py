from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.entities import Outcome


class MatchPredictionResponse(BaseModel):
    slate_id: str
    position: int
    match_id: str
    competition_name: str
    home_team_name: str
    away_team_name: str
    generated_at: datetime
    home_probability: float = Field(ge=0.0, le=1.0)
    draw_probability: float = Field(ge=0.0, le=1.0)
    away_probability: float = Field(ge=0.0, le=1.0)
    recommended_outcome: Outcome
    competition_readiness: str
    live_pick_allowed: bool
    policy_reason: str
    confidence_band: str
    rationale: list[str]
    # True when the slate operator (or the upstream parser) flagged
    # this position as a knockout / final: the boleta in those
    # positions cannot resolve to a draw, so `recommended_outcome` is
    # never "X" even if the raw model thought X was most likely.
    is_knockout: bool = False


class SlateFeatureResponse(BaseModel):
    slate_id: str
    features: list[dict[str, object]]


class TicketDecisionResponse(BaseModel):
    pick_type: str
    picks: list[Outcome]
    source: str = "model"


class TicketValidationResponse(BaseModel):
    level: str
    label: str
    recommendation: str
    reasons: list[str]
    metrics: dict[str, str | int | float | bool]


class MatchTicketRecommendationResponse(BaseModel):
    position: int
    match_id: str
    decisions: dict[str, TicketDecisionResponse]
    validation: TicketValidationResponse


class TicketCoverageMode(BaseModel):
    """Coverage projection for one ticket mode (simple / doubles / full).

    `probabilities_at_least` is keyed by the floor K (string-cast so the
    JSON payload is portable). `expected_correct` is E[hits] under that
    mode's decisions. Used by the UI to show 'P(>= 8/9 correct) = 0.74'.

    The honest jackpot fields are the ones the user actually cares about:
    * `jackpot_probability`: P(N/N correct) — the only tier Progol Media
      Semana pays. For weekend Progol the "near-jackpot" tier may also
      pay depending on year, so we expose both and let the UI label.
    * `near_jackpot_probability`: P(>= N-1/N correct).
    * `tickets_for_half_chance`: how many INDEPENDENT boletas you'd need
      so the cumulative probability of at least one jackpot exceeds 50%.
      `None` when the per-ticket probability is too small or too large
      to compute the inverse cleanly.
    """

    mode: str
    expected_correct: float
    probabilities_at_least: dict[str, float]
    target_floor: int
    target_probability: float
    target_met: bool
    jackpot_probability: float = 0.0
    near_jackpot_probability: float = 0.0
    tickets_for_half_chance: int | None = None


class TicketRecommendationResponse(BaseModel):
    slate_id: str
    snapshot_id: str
    generated_at: datetime
    model_version: str
    rules: dict[str, str | int]
    recommendations: list[MatchTicketRecommendationResponse]
    coverage: list[TicketCoverageMode] = Field(default_factory=list)
