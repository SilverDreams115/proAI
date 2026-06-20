"""Inactive controlled-gate predicate for the team rating (R5.0).

A PURE decision function: given the per-match rating/sanity facts and the gate
config, decide whether a match WOULD be routed to a (future) rating-aware model
arm. It is intentionally NOT imported by PredictionService or FeatureService —
wiring it in is a later, separately-authorized step.

Default-OFF guarantee: with ``settings.team_rating_gate_enabled`` false (the
default), :func:`evaluate_team_rating_gate` always returns
``eligible=False`` with reason ``flag_disabled`` BEFORE evaluating anything
else, so production behaviour and probabilities are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from collections.abc import Iterable

from app.core.settings import settings
from app.domain.team_rating_gate_config import CONFIDENT_BUCKETS
from app.domain.team_rating_gate_config import CRITICAL_SANITY_BLOCKERS


@dataclass(frozen=True)
class GateDecision:
    eligible: bool
    reason: str
    blockers: list[str] = field(default_factory=list)


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def evaluate_team_rating_gate(
    *,
    competition_name: str,
    rating_present: bool,
    both_rating_medium_plus: bool,
    home_rating_confidence: str,
    away_rating_confidence: str,
    calibrator_available: bool,
    sanity_flags: Iterable[str] | None = None,
    feature_flag_enabled: bool | None = None,
    gate_competitions: Iterable[str] | None = None,
    require_both_medium_plus: bool | None = None,
    require_calibrator: bool | None = None,
) -> GateDecision:
    """Decide whether a match is eligible for the rating-aware arm.

    All gate config defaults are read from ``settings`` (all OFF by default).
    ``feature_flag_enabled`` defaults to ``settings.team_rating_gate_enabled``.
    Returns a :class:`GateDecision`; in production today this is always
    ``eligible=False`` (``flag_disabled``).
    """
    enabled = (
        settings.team_rating_gate_enabled
        if feature_flag_enabled is None
        else feature_flag_enabled
    )
    # Hard short-circuit: flag off → never eligible, nothing else evaluated.
    if not enabled:
        return GateDecision(False, "flag_disabled", ["flag_disabled"])

    competitions = (
        settings.team_rating_gate_competitions
        if gate_competitions is None
        else list(gate_competitions)
    )
    require_bmp = (
        settings.team_rating_gate_require_both_medium_plus
        if require_both_medium_plus is None
        else require_both_medium_plus
    )
    require_cal = (
        settings.team_rating_gate_require_calibrator
        if require_calibrator is None
        else require_calibrator
    )
    allowed = {_normalize(c) for c in competitions}
    flags = list(sanity_flags or [])

    blockers: list[str] = []
    if _normalize(competition_name) not in allowed:
        blockers.append("competition_not_allowed")
    if not rating_present:
        blockers.append("rating_not_present")
    if require_bmp and not both_rating_medium_plus:
        blockers.append("not_both_medium_plus")
    if _normalize(home_rating_confidence) not in CONFIDENT_BUCKETS:
        blockers.append("home_confidence_too_low")
    if _normalize(away_rating_confidence) not in CONFIDENT_BUCKETS:
        blockers.append("away_confidence_too_low")
    if require_cal and not calibrator_available:
        blockers.append("calibrator_unavailable")
    hit = [f for f in flags if str(f).strip().upper() in CRITICAL_SANITY_BLOCKERS]
    if hit:
        blockers.append("sanity_blocked")

    if blockers:
        return GateDecision(False, "blocked", blockers)
    return GateDecision(True, "eligible", [])
