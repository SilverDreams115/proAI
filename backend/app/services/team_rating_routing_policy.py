"""Pure shadow routing policy for a future team-rating route (R5.2).

This module classifies legacy sanity flags for audit purposes only. It is not
imported by productive prediction, feature, ticket, optimizer, or approval
flows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


RoutingPolicyName = Literal[
    "strict",
    "rating_replaces_fallback",
    "review_allowed_shadow",
]

CLI_ROUTING_POLICIES = (
    "strict",
    "rating-replaces-fallback",
    "review-allowed-shadow",
)

HARD_SANITY_BLOCKERS = frozenset(
    {
        "BLOCKED",
        "EXTREME_PROBABILITY_WITHOUT_EVIDENCE",
        "DATA_CONFLICT",
        "PLACEHOLDER_TEAM",
        "RESULT_CONFLICT",
    }
)
SOFT_SANITY_BLOCKERS = frozenset({"FALLBACK_USED", "LOW_EVIDENCE"})
REVIEW_SANITY_BLOCKERS = frozenset({"REVISAR"})
ALL_ROUTING_SANITY_FLAGS = (
    HARD_SANITY_BLOCKERS | SOFT_SANITY_BLOCKERS | REVIEW_SANITY_BLOCKERS
)


@dataclass(frozen=True)
class TeamRatingRoutingPolicyDecision:
    policy: RoutingPolicyName
    eligible_for_rating_route: bool
    blockers: list[str]
    hard_sanity_blockers: list[str]
    soft_sanity_blockers: list[str]
    review_blockers: list[str]
    warnings: list[str]


def normalize_routing_policy(name: str) -> RoutingPolicyName:
    normalized = name.strip().lower().replace("-", "_")
    if normalized not in {
        "strict",
        "rating_replaces_fallback",
        "review_allowed_shadow",
    }:
        raise ValueError(f"unsupported routing policy {name!r}")
    return normalized  # type: ignore[return-value]


def _normalize_flags(flags: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for flag in flags or ():
        normalized = str(flag).strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _policy_warning(prefix: str, flag: str) -> str:
    return f"{prefix}:{flag}"


def evaluate_team_rating_routing_policy(
    *,
    policy: RoutingPolicyName | str,
    gate_eligible_if_enabled: bool,
    gate_blockers: Iterable[str] | None,
    both_medium_plus: bool,
    calibrator_available: bool,
    sanity_flags: Iterable[str] | None = None,
) -> TeamRatingRoutingPolicyDecision:
    """Decide whether a shadow rating route would be allowed.

    The gate/rating/calibrator guard must pass before this policy considers
    legacy sanity flags. If that guard fails, the policy reports the gate
    blockers and does not reclassify unrelated legacy fallback flags as the
    active reason.
    """
    selected = normalize_routing_policy(policy)
    blockers = list(gate_blockers or [])
    if not both_medium_plus and "not_both_medium_plus" not in blockers:
        blockers.append("not_both_medium_plus")
    if not calibrator_available and "calibrator_unavailable" not in blockers:
        blockers.append("calibrator_unavailable")

    gate_passed = gate_eligible_if_enabled and both_medium_plus and calibrator_available
    if not gate_passed:
        return TeamRatingRoutingPolicyDecision(
            policy=selected,
            eligible_for_rating_route=False,
            blockers=blockers,
            hard_sanity_blockers=[],
            soft_sanity_blockers=[],
            review_blockers=[],
            warnings=[],
        )

    flags = _normalize_flags(sanity_flags)
    hard_hits = [flag for flag in flags if flag in HARD_SANITY_BLOCKERS]
    soft_hits = [flag for flag in flags if flag in SOFT_SANITY_BLOCKERS]
    review_hits = [flag for flag in flags if flag in REVIEW_SANITY_BLOCKERS]
    warnings: list[str] = []

    if hard_hits:
        blockers.append("hard_sanity_blocked")
    if selected == "strict":
        if soft_hits:
            blockers.append("soft_sanity_blocked")
        if review_hits:
            blockers.append("review_blocked")
        soft_blockers = soft_hits
        review_blockers = review_hits
    elif selected == "rating_replaces_fallback":
        warnings.extend(_policy_warning("soft_sanity_allowed", flag) for flag in soft_hits)
        if review_hits:
            blockers.append("review_blocked")
        soft_blockers = []
        review_blockers = review_hits
    else:
        warnings.extend(_policy_warning("soft_sanity_allowed", flag) for flag in soft_hits)
        warnings.extend(_policy_warning("review_allowed", flag) for flag in review_hits)
        soft_blockers = []
        review_blockers = []

    return TeamRatingRoutingPolicyDecision(
        policy=selected,
        eligible_for_rating_route=not blockers,
        blockers=blockers,
        hard_sanity_blockers=hard_hits,
        soft_sanity_blockers=soft_blockers,
        review_blockers=review_blockers,
        warnings=warnings,
    )
