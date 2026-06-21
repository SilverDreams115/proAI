"""Response schemas for the Team Rating activation dry-run (R5.5).

Strictly diagnostic: simulates what enabling the controlled team-rating gate
would do to engines / probabilities / picks, without ever changing real
predictions, picks, tickets, probabilities or the approval gate.
"""
from __future__ import annotations

from pydantic import BaseModel


class DryRunActivationPolicy(BaseModel):
    competition_allowlist: list[str]
    routing_policy: str
    calibrator_candidate: str
    temperature: float
    require_both_medium_plus: bool
    require_calibrator_compatible: bool
    review_blocks: bool


class DryRunCalibrator(BaseModel):
    id: str
    temperature: float
    productive_available: bool
    compatible: bool
    compatibility_blockers: list[str]


class DryRunSummary(BaseModel):
    total_matches: int
    eligible_if_enabled: int
    would_route: int
    would_keep_current: int
    blocked_by_rating: int
    blocked_by_review: int
    blocked_by_hard_sanity: int
    changed_top_pick_count: int
    changed_confidence_bucket_count: int
    max_probability_delta: float
    positions_would_route: list[int]
    positions_changed_pick: list[int]


class DryRunMatch(BaseModel):
    position: int
    match_id: str
    home_team: str
    away_team: str
    competition: str
    current_engine: str
    dry_run_engine: str
    would_route: bool
    current_probabilities: dict[str, float] | None
    dry_run_probabilities: dict[str, float] | None
    probability_delta: dict[str, float] | None
    max_abs_delta: float
    current_top_pick: str | None
    dry_run_top_pick: str | None
    top_pick_changed: bool
    current_confidence_bucket: str | None
    dry_run_confidence_bucket: str | None
    confidence_bucket_changed: bool
    blockers: list[str]
    warnings: list[str]


class TeamRatingActivationDryRunResponse(BaseModel):
    slate_id: str
    draw_code: str | None
    mode: str
    production_active: bool
    safe_to_activate: bool
    dry_run_probability_model: str
    activation_policy: DryRunActivationPolicy
    calibrator: DryRunCalibrator | None
    summary: DryRunSummary
    matches: list[DryRunMatch]
    activation_blockers: list[str]
