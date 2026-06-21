"""Response schemas for the read-only Team Rating Shadow diagnostic (R5.4).

This payload is shadow-only: it describes what the inactive team-rating gate
would do if it were enabled, without ever changing predictions, picks, tickets
or probabilities.
"""
from __future__ import annotations

from pydantic import BaseModel


class ShadowActiveRun(BaseModel):
    run_id: str
    algorithm_version: str
    status: str
    snapshot_count: int


class ShadowCalibratorCandidate(BaseModel):
    id: str
    competition: str
    temperature: float
    routing_policy: str
    productive_available: bool
    compatible: bool
    compatibility_blockers: list[str]


class ShadowSummary(BaseModel):
    total_matches: int
    eligible_current: int
    eligible_if_enabled: int
    would_use_rating_model_current: int
    would_use_rating_model_if_enabled: int
    would_remain_fallback: int
    blocked_by_flag: int
    blocked_by_competition: int
    blocked_by_rating: int
    blocked_by_calibrator: int
    blocked_by_sanity: int
    warnings: int
    positions_eligible_if_enabled: list[int]
    positions_would_route: list[int]
    positions_blocked: list[int]


class ShadowMatch(BaseModel):
    position: int
    match_id: str
    home_team: str
    away_team: str
    competition: str
    rating_status: str
    rating_diff: float | None
    both_medium_plus: bool
    eligible_current: bool
    eligible_if_enabled: bool
    would_use_rating_model_if_enabled: bool
    blockers: list[str]
    warnings: list[str]


class TeamRatingShadowResponse(BaseModel):
    slate_id: str
    draw_code: str | None
    mode: str
    production_active: bool
    feature_flag_enabled: bool
    gate_flag_enabled: bool
    routing_policy: str
    active_rating_run: ShadowActiveRun
    calibrator_candidate: ShadowCalibratorCandidate | None
    summary: ShadowSummary
    matches: list[ShadowMatch]
