"""Response schema for the team-rating canary status endpoint (R5.6-B)."""
from __future__ import annotations

from pydantic import BaseModel


class TeamRatingCanaryStatusResponse(BaseModel):
    canary_enabled: bool
    scope: str | None
    in_scope: bool
    competition_allowlist: list[str]
    routing_policy: str
    calibrator_id: str
    temperature: float
    allowed_positions: list[int]
    active_positions: list[int]
    blocked_positions: list[int]
    calibrator_compatibility_blockers: list[str] = []
    full_activation: bool
    ticket_integration: bool
    rollback_available: bool
