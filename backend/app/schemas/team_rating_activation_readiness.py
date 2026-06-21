"""Response schemas for the Team Rating activation readiness report (R5.6-A).

Read-only and diagnostic only: it reports whether the technical blockers before a
minimal canary are cleared, without activating the gate or changing any real
prediction, pick, ticket, probability or approval gate.
"""
from __future__ import annotations

from pydantic import BaseModel


class ReadinessTargetActivation(BaseModel):
    scope: str
    competition_allowlist: list[str]
    routing_policy: str
    calibrator_id: str
    temperature: float
    require_both_medium_plus: bool
    review_blocks: bool
    hard_blockers_block: bool


class ReadinessCalibrator(BaseModel):
    id: str
    approval_status: str
    approved_for_canary: bool
    productive_available: bool
    active: bool


class ReadinessDryRunSummary(BaseModel):
    total_matches: int
    would_route: int
    would_keep_current: int
    changed_top_pick_count: int
    max_probability_delta: float


class ReadinessCheck(BaseModel):
    check: str
    status: str
    details: str | None = None
    count: int | None = None


class ReadinessCanaryPlan(BaseModel):
    canary_allowed_matches: list[int]
    blocked_matches: list[int]
    rollback: list[str]


class TeamRatingActivationReadinessResponse(BaseModel):
    slate_id: str
    draw_code: str | None
    mode: str
    production_active: bool
    ready_for_canary: bool
    ready_for_full_activation: bool
    target_activation: ReadinessTargetActivation
    calibrator: ReadinessCalibrator
    dry_run_summary: ReadinessDryRunSummary
    readiness_checks: list[ReadinessCheck]
    canary_plan: ReadinessCanaryPlan
