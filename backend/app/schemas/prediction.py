from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.entities import Outcome


class MatchCanaryInfo(BaseModel):
    """Per-match controlled-canary metadata (R5.6-B). Diagnostic only — it
    never changes the persisted prediction, the ticket, or the legacy
    probability fields."""

    active: bool = False
    engine: str = "current"
    applied: bool = False
    original_display_probabilities: dict[str, float] | None = None
    probability_delta: dict[str, float] | None = None
    max_abs_delta: float = 0.0
    original_top_pick: str | None = None
    effective_top_pick: str | None = None
    top_pick_changed: bool = False
    ticket_uses_canary: bool = False
    warnings: list[str] = Field(default_factory=list)


class PresentationGuardInfo(BaseModel):
    """R5.6-D read-only presentation contract.

    Prevents the UI from showing a risky/blocked prediction as a simple
    playable suggestion. Derived from existing sanity metadata; never changes
    probabilities or persisted data.
    """

    simple_allowed: bool = False
    primary_signal: str = ""
    recommendation_label: str = "NO SIMPLE"
    risk_level: str = "high"
    confidence: str = "baja"
    reason: list[str] = Field(default_factory=list)


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

    # --- Sanity layer (Fase 3/4): explicit, non-positional outputs -------
    #
    # THREE explicit probability vectors, all keyed by the Progol outcome
    # codes (L/E/V) so nothing ever relies on array order. The contract is:
    #
    #   raw_probabilities      -> the ORIGINAL model / fallback output.
    #                             Preserved untouched for full traceability.
    #   display_probabilities  -> the guardrailed vector shown in the UI.
    #   decision_probabilities -> the guardrailed vector the ticket
    #                             optimizer / coverage math MUST consume.
    #
    # `display_probabilities` and `decision_probabilities` are equal by
    # design today — both are the single sanity-degraded vector — but they
    # are separate fields so display and decision could diverge later
    # without another schema migration.
    #
    # `probabilities` is kept as an alias of `decision_probabilities` (the
    # guardrailed numbers) for backward compatibility; downstream code
    # should prefer the explicit `decision_probabilities` field.
    probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    display_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    decision_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    labels: dict[str, str] = Field(
        default_factory=lambda: {"L": "Local", "E": "Empate", "V": "Visitante"}
    )
    # The pre-sanity vector, preserved for full traceability — the sanity
    # layer never silently rewrites a probability without exposing the raw.
    raw_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    evidence_level: str = "low"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # UI-facing confidence label — authoritative, never contradicts flags.
    # One of "alta" / "media" / "media-baja" / "baja". The client renders
    # this directly and must NOT recompute "alta" from confidence_band.
    visible_confidence: str = "baja"
    # Up to 3 short reasons (also used as the card's visible "Motivos").
    confidence_explanation: list[str] = Field(default_factory=list)
    risk_level: str = "high"
    # FIJO / LISTO / REVISAR / BLOQUEADO — the guardrailed status the UI
    # must render. Distinct from `confidence_band` (the model's own band).
    final_status: str = "REVISAR"
    # Backend-authoritative boleta strategy — the UI renders this directly
    # (never "Fijo"). One of SIMPLE / DOBLE_RECOMENDADO / TRIPLE_RECOMENDADO
    # / NO_DEJAR_SIMPLE / EVITAR. The client may upgrade to TRIPLE only when
    # the optimizer allocates a triple (coverage refinement, not a downgrade).
    ticket_strategy: str = "NO_DEJAR_SIMPLE"
    ticket_strategy_label: str = "No dejar simple"
    ticket_strategy_reason: str = ""
    flags: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    is_international_friendly: bool = False
    sanity_recommendation: str = ""

    # --- Conservative draw (X) calibration (additive) --------------------
    # When True, the decision vector above had its p_draw nudged up toward a
    # conservative prior on a low-evidence / high-uncertainty match. The raw
    # vector is unchanged; pre_draw_calibration_probabilities is the decision
    # vector BEFORE the nudge, surfaced in the technical detail.
    draw_calibration_applied: bool = False
    draw_calibration_reason: str | None = None
    pre_draw_calibration_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )

    # --- R5.6-B controlled canary (additive, never overwrites the originals) -
    # `effective_*` are what the UI should render when `canary.active` is true.
    # When the canary is OFF or a position is out of scope they equal the
    # `display_/decision_probabilities` above, so existing consumers are
    # unaffected. The persisted prediction and the legacy fields never change.
    effective_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    effective_decision_probabilities: dict[str, float] = Field(
        default_factory=lambda: {"L": 0.0, "E": 0.0, "V": 0.0}
    )
    canary: MatchCanaryInfo | None = None

    # --- R5.6-D presentation guard (read-only, derived) ------------------
    presentation_guard: PresentationGuardInfo | None = None

    # --- Accessors used by the ticket optimizer / coverage math ----------
    # Single chokepoint so decision code never reaches for the legacy
    # positional fields by accident.
    def decision_vector(self) -> tuple[float, float, float]:
        """Return ``(home, draw, away)`` from `decision_probabilities`.

        Falls back to the legacy positional fields only when the decision
        vector is unpopulated (sums to ~0) — i.e. for hand-built test
        fixtures or pre-sanity payloads."""
        vector = self.decision_probabilities or {}
        if {"L", "E", "V"} <= set(vector):
            home, draw, away = float(vector["L"]), float(vector["E"]), float(vector["V"])
            if (home + draw + away) > 1e-9:
                return home, draw, away
        return float(self.home_probability), float(self.draw_probability), float(self.away_probability)

    def decision_draw_probability(self) -> float:
        return self.decision_vector()[1]


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


class DrawRiskResponse(BaseModel):
    """Draw-risk projection for one match (reporting only).

    These fields never alter probabilities, picks, or confidence bands —
    they expose, per match, how much draw mass the model assigned and
    whether the empate is actually covered in each ticket mode. The UI
    uses them to surface "Empate vivo" / "Empate fuerte" chips so the
    operator can see at a glance why draws may be hurting the boleta.

    Thresholds are draw-reporting thresholds, independent of the model's
    confidence-band thresholds:
    * `is_live_draw`   -> p_draw >= 0.25
    * `is_strong_draw` -> p_draw >= 0.30
    `draw_rank` is the rank of the draw among the three outcomes by
    probability (1 = most likely, 3 = least likely).
    """

    p_draw: float
    draw_rank: int
    is_live_draw: bool
    is_strong_draw: bool
    covered_simple: bool
    covered_doubles: bool
    covered_full: bool


class MatchTicketRecommendationResponse(BaseModel):
    position: int
    match_id: str
    decisions: dict[str, TicketDecisionResponse]
    validation: TicketValidationResponse
    draw_risk: DrawRiskResponse | None = None


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
