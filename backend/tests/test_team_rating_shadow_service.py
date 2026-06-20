"""R5.1: shadow-only team-rating gate decisions."""

from __future__ import annotations

from app.services.team_rating_shadow_service import TeamRatingShadowFacts
from app.services.team_rating_shadow_service import evaluate_team_rating_shadow_for_match
from app.services.team_rating_shadow_service import evaluate_team_rating_shadow_for_slate
from app.services.team_rating_shadow_service import has_rating_blocker


def _eligible_kwargs(**over):
    base = dict(
        competition_name="International Friendlies",
        rating_present=True,
        both_rating_medium_plus=True,
        home_rating_confidence="strong",
        away_rating_confidence="medium",
        rating_diff=55.0,
        sanity_flags=[],
        gate_enabled=False,
        productive_calibrator_available=False,
    )
    base.update(over)
    return base


def test_flag_off_blocks_current_without_shadow_activation():
    decision = evaluate_team_rating_shadow_for_match(**_eligible_kwargs())
    assert decision.shadow_enabled is False
    assert decision.gate_enabled is False
    assert decision.eligible_current is False
    assert decision.eligible_if_enabled is False
    assert decision.would_use_rating_model is False
    assert decision.would_remain_fallback is True
    assert decision.blockers == ["flag_disabled"]


def test_assume_gate_enabled_calculates_if_enabled_path():
    decision = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(
            assume_gate_enabled=True,
            assume_calibrator_available=True,
        )
    )
    assert decision.shadow_enabled is True
    assert decision.eligible_current is False
    assert decision.eligible_if_enabled is True
    assert decision.would_use_rating_model is True
    assert decision.would_remain_fallback is False
    assert decision.calibrator_available is True
    assert decision.rating_diff == 55.0


def test_assume_calibrator_available_only_affects_shadow_simulation():
    no_cal = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(assume_gate_enabled=True)
    )
    with_cal = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(
            assume_gate_enabled=True,
            assume_calibrator_available=True,
        )
    )
    assert no_cal.eligible_current is False
    assert no_cal.eligible_if_enabled is False
    assert "calibrator_unavailable" in no_cal.blockers
    assert with_cal.eligible_current is False
    assert with_cal.eligible_if_enabled is True
    assert with_cal.would_use_rating_model is True


def test_partial_rating_blocks_shadow_route():
    decision = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(
            rating_present=False,
            both_rating_medium_plus=False,
            away_rating_confidence="no_rating",
            rating_diff=None,
            assume_gate_enabled=True,
            assume_calibrator_available=True,
        )
    )
    assert decision.eligible_if_enabled is False
    assert decision.would_use_rating_model is False
    assert "rating_not_present" in decision.blockers
    assert "not_both_medium_plus" in decision.blockers
    assert has_rating_blocker(decision) is True
    assert decision.rating_diff is None


def test_competition_not_allowed_blocks():
    decision = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(
            competition_name="Copa Libertadores",
            assume_gate_enabled=True,
            assume_calibrator_available=True,
        )
    )
    assert decision.eligible_if_enabled is False
    assert decision.would_use_rating_model is False
    assert "competition_not_allowed" in decision.blockers


def test_sanity_blockers_hold_final_route_after_rating_guard_passes():
    decision = evaluate_team_rating_shadow_for_match(
        **_eligible_kwargs(
            sanity_flags=["FALLBACK_USED"],
            assume_gate_enabled=True,
            assume_calibrator_available=True,
        )
    )
    assert decision.eligible_if_enabled is True
    assert decision.would_use_rating_model is False
    assert decision.would_remain_fallback is True
    assert "sanity_blocked" in decision.blockers


def test_slate_helper_evaluates_sequence():
    facts = [
        TeamRatingShadowFacts(
            competition_name="International Friendlies",
            rating_present=True,
            both_rating_medium_plus=True,
            home_rating_confidence="medium",
            away_rating_confidence="strong",
            rating_diff=10.0,
        ),
        TeamRatingShadowFacts(
            competition_name="Brasileirao",
            rating_present=True,
            both_rating_medium_plus=True,
            home_rating_confidence="medium",
            away_rating_confidence="strong",
            rating_diff=-5.0,
        ),
    ]
    decisions = evaluate_team_rating_shadow_for_slate(
        facts,
        assume_gate_enabled=True,
        assume_calibrator_available=True,
        gate_enabled=False,
        productive_calibrator_available=False,
    )
    assert [d.eligible_if_enabled for d in decisions] == [True, False]
    assert "competition_not_allowed" in decisions[1].blockers
