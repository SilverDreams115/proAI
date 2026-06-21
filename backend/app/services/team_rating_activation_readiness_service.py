"""Activation-readiness report for the team-rating gate (R5.6-A).

Answers "is everything cleared for a minimal canary, except the deliberate
flag flip?" It reuses the R5.5 dry-run payload (which itself reuses the audited
shadow/gate/routing/calibrator rules) and adds readiness checks, a canary plan
and the calibrator approval state.

Strictly read-only and diagnostic only: it never activates the gate, regenerates
predictions, loads a model artifact, or changes real probabilities / picks /
tickets / approval gate, and it writes no row.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.domain.team_rating_calibrator import get_team_rating_calibrator_candidate
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.schemas.team_rating_activation_readiness import (
    TeamRatingActivationReadinessResponse,
)
from app.services.team_rating_activation_dry_run_service import (
    DRY_RUN_CALIBRATOR_CANDIDATE_ID,
    DRY_RUN_ROUTING_POLICY,
    build_activation_dry_run_payload,
)

ROLLBACK_PLAN = [
    "set team_rating_gate_enabled=false",
    "set team_rating_feature_enabled=false",
    "restart proai and worker",
    "verify predictions / match_feature_snapshots / ticket_recommendation_snapshots counts unchanged",
]


def _readiness_checks(
    *,
    gate_enabled: bool,
    approved_for_canary: bool,
    productive_available: bool,
    blocked_by_hard_sanity: int,
    blocked_by_review: int,
    blocked_by_rating: int,
) -> list[dict[str, Any]]:
    return [
        {
            "check": "feature_flag_off",
            "status": "pass" if gate_enabled else "blocking_until_canary",
            "details": "team_rating_gate_enabled must be flipped on in R5.6-B",
            "count": None,
        },
        {
            "check": "calibrator_approved_inactive",
            "status": "pass" if approved_for_canary else "blocking",
            "details": "calibrator approved for canary while staying inactive",
            "count": None,
        },
        {
            "check": "calibrator_productive_available",
            "status": "pass" if productive_available else "blocking_until_full_activation",
            "details": "a productive artifact is required for full activation, not canary",
            "count": None,
        },
        {
            "check": "hard_sanity_blockers_present",
            "status": "block_for_affected_matches" if blocked_by_hard_sanity else "pass",
            "details": "matches with hard sanity blockers never route",
            "count": blocked_by_hard_sanity,
        },
        {
            "check": "review_blockers_present",
            "status": "block_for_affected_matches" if blocked_by_review else "pass",
            "details": "REVISAR / review_blocked keep matches on the current engine",
            "count": blocked_by_review,
        },
        {
            "check": "rating_coverage",
            "status": "partial_pass" if blocked_by_rating else "pass",
            "details": "matches without full both-sides rating cannot route",
            "count": blocked_by_rating,
        },
        {
            "check": "read_only_guards",
            "status": "pass",
            "details": "readiness / dry-run / shadow paths are read-only",
            "count": None,
        },
    ]


def build_activation_readiness_payload(
    session: Session,
    links: list[ProgolSlateMatchModel],
    *,
    slate_id: str,
    draw_code: str | None,
) -> dict[str, Any]:
    """Core read-only builder working on a list of slate-match links."""
    dry_run = build_activation_dry_run_payload(
        session, links, slate_id=slate_id, draw_code=draw_code
    )
    candidate = get_team_rating_calibrator_candidate(DRY_RUN_CALIBRATOR_CANDIDATE_ID)
    s = dry_run["summary"]

    gate_enabled = bool(settings.team_rating_gate_enabled)
    approved_for_canary = candidate.approval_status == "approved_inactive"
    productive_available = bool(candidate.productive_available)

    checks = _readiness_checks(
        gate_enabled=gate_enabled,
        approved_for_canary=approved_for_canary,
        productive_available=productive_available,
        blocked_by_hard_sanity=s["blocked_by_hard_sanity"],
        blocked_by_review=s["blocked_by_review"],
        blocked_by_rating=s["blocked_by_rating"],
    )
    statuses = {c["status"] for c in checks}
    ready_for_canary = "blocking_until_canary" not in statuses and "blocking" not in statuses
    ready_for_full_activation = (
        ready_for_canary and "blocking_until_full_activation" not in statuses
    )

    allowed = list(s["positions_would_route"])
    allowed_set = set(allowed)
    blocked = [
        int(m["position"]) for m in dry_run["matches"] if int(m["position"]) not in allowed_set
    ]

    return {
        "slate_id": slate_id,
        "draw_code": draw_code,
        "mode": "activation_readiness",
        "production_active": False,
        "ready_for_canary": ready_for_canary,
        "ready_for_full_activation": ready_for_full_activation,
        "target_activation": {
            "scope": "minimal_canary",
            "competition_allowlist": list(settings.team_rating_gate_competitions),
            "routing_policy": DRY_RUN_ROUTING_POLICY,
            "calibrator_id": DRY_RUN_CALIBRATOR_CANDIDATE_ID,
            "temperature": candidate.temperature,
            "require_both_medium_plus": settings.team_rating_gate_require_both_medium_plus,
            "review_blocks": True,
            "hard_blockers_block": True,
        },
        "calibrator": {
            "id": candidate.candidate_id,
            "approval_status": candidate.approval_status,
            "approved_for_canary": approved_for_canary,
            "productive_available": productive_available,
            "active": bool(candidate.active),
        },
        "dry_run_summary": {
            "total_matches": s["total_matches"],
            "would_route": s["would_route"],
            "would_keep_current": s["would_keep_current"],
            "changed_top_pick_count": s["changed_top_pick_count"],
            "max_probability_delta": s["max_probability_delta"],
        },
        "readiness_checks": checks,
        "canary_plan": {
            "canary_allowed_matches": allowed,
            "blocked_matches": sorted(blocked),
            "rollback": list(ROLLBACK_PLAN),
        },
    }


def build_slate_activation_readiness(
    session: Session, slate: ProgolSlateModel
) -> TeamRatingActivationReadinessResponse:
    links = sorted(slate.matches, key=lambda link: link.position)
    payload = build_activation_readiness_payload(
        session,
        links,
        slate_id=slate.id,
        draw_code=getattr(slate, "draw_code", None),
    )
    return TeamRatingActivationReadinessResponse(**payload)
