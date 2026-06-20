"""Shadow-only evaluator for the inactive team-rating gate (R5.1).

This module is deliberately isolated from productive services. It does not
open DB sessions, write rows, load model artifacts, or change probabilities.
Callers pass already-read rating/sanity facts and receive an audit decision
describing what the inactive gate does today and what a hypothetical shadow
run would do.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.settings import settings
from app.domain.team_rating_gate_config import GATE_CALIBRATOR_METADATA
from app.services.team_rating_gate_service import evaluate_team_rating_gate


_RATING_BLOCKERS = frozenset(
    {
        "rating_not_present",
        "not_both_medium_plus",
        "home_confidence_too_low",
        "away_confidence_too_low",
    }
)


@dataclass(frozen=True)
class TeamRatingShadowDecision:
    shadow_enabled: bool
    gate_enabled: bool
    eligible_current: bool
    eligible_if_enabled: bool
    would_use_rating_model: bool
    would_remain_fallback: bool
    blockers: list[str]
    rating_diff: float | None
    both_medium_plus: bool
    calibrator_required: bool
    calibrator_available: bool


@dataclass(frozen=True)
class TeamRatingShadowFacts:
    competition_name: str
    rating_present: bool
    both_rating_medium_plus: bool
    home_rating_confidence: str
    away_rating_confidence: str
    rating_diff: float | None
    sanity_flags: tuple[str, ...] = ()


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def evaluate_team_rating_shadow_for_match(
    *,
    competition_name: str,
    rating_present: bool,
    both_rating_medium_plus: bool,
    home_rating_confidence: str,
    away_rating_confidence: str,
    rating_diff: float | None,
    sanity_flags: Iterable[str] | None = None,
    assume_gate_enabled: bool = False,
    assume_calibrator_available: bool = False,
    gate_enabled: bool | None = None,
    gate_competitions: Iterable[str] | None = None,
    require_both_medium_plus: bool | None = None,
    require_calibrator: bool | None = None,
    productive_calibrator_available: bool | None = None,
) -> TeamRatingShadowDecision:
    """Evaluate current OFF behaviour and an optional shadow scenario.

    ``eligible_current`` always uses the real/current gate flag. With the
    default settings that is ``False`` and the pure gate returns
    ``flag_disabled`` before evaluating rating facts.

    ``eligible_if_enabled`` is populated only when ``assume_gate_enabled`` is
    true. It answers whether the match clears the rating/competition/calibrator
    guard if the gate were on, intentionally ignoring legacy sanity flags.

    ``would_use_rating_model`` adds the current sanity blockers on top of that
    shadow guard. It remains false unless the caller explicitly assumes the
    gate is enabled.
    """
    current_gate_enabled = (
        settings.team_rating_gate_enabled if gate_enabled is None else gate_enabled
    )
    require_cal = (
        settings.team_rating_gate_require_calibrator
        if require_calibrator is None
        else require_calibrator
    )
    productive_cal = (
        GATE_CALIBRATOR_METADATA.productive_calibrator_available
        if productive_calibrator_available is None
        else productive_calibrator_available
    )
    shadow_calibrator_available = (
        True if assume_calibrator_available else productive_cal
    )
    flags = tuple(sanity_flags or ())

    current = evaluate_team_rating_gate(
        competition_name=competition_name,
        rating_present=rating_present,
        both_rating_medium_plus=both_rating_medium_plus,
        home_rating_confidence=home_rating_confidence,
        away_rating_confidence=away_rating_confidence,
        calibrator_available=productive_cal,
        sanity_flags=flags,
        feature_flag_enabled=current_gate_enabled,
        gate_competitions=gate_competitions,
        require_both_medium_plus=require_both_medium_plus,
        require_calibrator=require_cal,
    )
    if not assume_gate_enabled:
        return TeamRatingShadowDecision(
            shadow_enabled=False,
            gate_enabled=current_gate_enabled,
            eligible_current=current.eligible,
            eligible_if_enabled=False,
            would_use_rating_model=current.eligible,
            would_remain_fallback=not current.eligible,
            blockers=current.blockers,
            rating_diff=rating_diff if rating_present else None,
            both_medium_plus=both_rating_medium_plus,
            calibrator_required=require_cal,
            calibrator_available=productive_cal,
        )

    guard = evaluate_team_rating_gate(
        competition_name=competition_name,
        rating_present=rating_present,
        both_rating_medium_plus=both_rating_medium_plus,
        home_rating_confidence=home_rating_confidence,
        away_rating_confidence=away_rating_confidence,
        calibrator_available=shadow_calibrator_available,
        sanity_flags=[],
        feature_flag_enabled=True,
        gate_competitions=gate_competitions,
        require_both_medium_plus=require_both_medium_plus,
        require_calibrator=require_cal,
    )
    full = evaluate_team_rating_gate(
        competition_name=competition_name,
        rating_present=rating_present,
        both_rating_medium_plus=both_rating_medium_plus,
        home_rating_confidence=home_rating_confidence,
        away_rating_confidence=away_rating_confidence,
        calibrator_available=shadow_calibrator_available,
        sanity_flags=flags,
        feature_flag_enabled=True,
        gate_competitions=gate_competitions,
        require_both_medium_plus=require_both_medium_plus,
        require_calibrator=require_cal,
    )
    blockers = full.blockers if full.blockers else guard.blockers
    if guard.eligible and not full.eligible:
        blockers = _dedupe([*blockers, *full.blockers])
    return TeamRatingShadowDecision(
        shadow_enabled=True,
        gate_enabled=current_gate_enabled,
        eligible_current=current.eligible,
        eligible_if_enabled=guard.eligible,
        would_use_rating_model=full.eligible,
        would_remain_fallback=not full.eligible,
        blockers=blockers,
        rating_diff=rating_diff if rating_present else None,
        both_medium_plus=both_rating_medium_plus,
        calibrator_required=require_cal,
        calibrator_available=shadow_calibrator_available,
    )


def evaluate_team_rating_shadow_for_slate(
    facts: Iterable[TeamRatingShadowFacts],
    *,
    assume_gate_enabled: bool = False,
    assume_calibrator_available: bool = False,
    gate_enabled: bool | None = None,
    gate_competitions: Iterable[str] | None = None,
    require_both_medium_plus: bool | None = None,
    require_calibrator: bool | None = None,
    productive_calibrator_available: bool | None = None,
) -> list[TeamRatingShadowDecision]:
    """Evaluate a sequence of already-loaded match facts without side effects."""
    return [
        evaluate_team_rating_shadow_for_match(
            competition_name=item.competition_name,
            rating_present=item.rating_present,
            both_rating_medium_plus=item.both_rating_medium_plus,
            home_rating_confidence=item.home_rating_confidence,
            away_rating_confidence=item.away_rating_confidence,
            rating_diff=item.rating_diff,
            sanity_flags=item.sanity_flags,
            assume_gate_enabled=assume_gate_enabled,
            assume_calibrator_available=assume_calibrator_available,
            gate_enabled=gate_enabled,
            gate_competitions=gate_competitions,
            require_both_medium_plus=require_both_medium_plus,
            require_calibrator=require_calibrator,
            productive_calibrator_available=productive_calibrator_available,
        )
        for item in facts
    ]


def has_rating_blocker(decision: TeamRatingShadowDecision) -> bool:
    """True when the shadow decision contains any rating-quality blocker."""
    return any(blocker in _RATING_BLOCKERS for blocker in decision.blockers)
