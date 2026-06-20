"""R5.2: pure routing policy for team-rating shadow audits."""

from __future__ import annotations

from app.services.team_rating_routing_policy import evaluate_team_rating_routing_policy
from app.services.team_rating_routing_policy import normalize_routing_policy


def _decision(**overrides):
    base = dict(
        policy="strict",
        gate_eligible_if_enabled=True,
        gate_blockers=[],
        both_medium_plus=True,
        calibrator_available=True,
        sanity_flags=[],
    )
    base.update(overrides)
    return evaluate_team_rating_routing_policy(**base)


def test_strict_blocks_fallback_used():
    decision = _decision(sanity_flags=["FALLBACK_USED"])
    assert decision.eligible_for_rating_route is False
    assert decision.soft_sanity_blockers == ["FALLBACK_USED"]
    assert "soft_sanity_blocked" in decision.blockers


def test_rating_replaces_fallback_allows_fallback_used_when_gate_passes():
    decision = _decision(
        policy="rating_replaces_fallback",
        sanity_flags=["FALLBACK_USED"],
    )
    assert decision.eligible_for_rating_route is True
    assert decision.soft_sanity_blockers == []
    assert decision.warnings == ["soft_sanity_allowed:FALLBACK_USED"]


def test_rating_replaces_fallback_allows_low_evidence_with_rating_and_calibrator():
    decision = _decision(
        policy="rating_replaces_fallback",
        sanity_flags=["LOW_EVIDENCE"],
    )
    assert decision.eligible_for_rating_route is True
    assert decision.warnings == ["soft_sanity_allowed:LOW_EVIDENCE"]


def test_hard_blockers_always_block():
    for flag in (
        "BLOCKED",
        "EXTREME_PROBABILITY_WITHOUT_EVIDENCE",
        "DATA_CONFLICT",
        "PLACEHOLDER_TEAM",
        "RESULT_CONFLICT",
    ):
        decision = _decision(
            policy="review_allowed_shadow",
            sanity_flags=[flag],
        )
        assert decision.eligible_for_rating_route is False
        assert decision.hard_sanity_blockers == [flag]
        assert "hard_sanity_blocked" in decision.blockers


def test_revisar_blocks_in_strict_and_rating_replaces_fallback():
    strict = _decision(policy="strict", sanity_flags=["REVISAR"])
    replacement = _decision(
        policy="rating_replaces_fallback",
        sanity_flags=["REVISAR"],
    )
    assert strict.eligible_for_rating_route is False
    assert replacement.eligible_for_rating_route is False
    assert strict.review_blockers == ["REVISAR"]
    assert replacement.review_blockers == ["REVISAR"]
    assert "review_blocked" in replacement.blockers


def test_review_allowed_shadow_warns_for_revisar():
    decision = _decision(
        policy="review_allowed_shadow",
        sanity_flags=["REVISAR"],
    )
    assert decision.eligible_for_rating_route is True
    assert decision.review_blockers == []
    assert decision.warnings == ["review_allowed:REVISAR"]


def test_partial_rating_still_blocks():
    decision = _decision(
        policy="rating_replaces_fallback",
        gate_eligible_if_enabled=False,
        gate_blockers=["rating_not_present", "not_both_medium_plus"],
        both_medium_plus=False,
        sanity_flags=["FALLBACK_USED"],
    )
    assert decision.eligible_for_rating_route is False
    assert "rating_not_present" in decision.blockers
    assert "not_both_medium_plus" in decision.blockers
    assert decision.soft_sanity_blockers == []


def test_missing_calibrator_still_blocks():
    decision = _decision(
        policy="rating_replaces_fallback",
        gate_eligible_if_enabled=False,
        gate_blockers=["calibrator_unavailable"],
        calibrator_available=False,
        sanity_flags=["LOW_EVIDENCE"],
    )
    assert decision.eligible_for_rating_route is False
    assert "calibrator_unavailable" in decision.blockers
    assert decision.warnings == []


def test_competition_not_allowed_still_blocks():
    decision = _decision(
        policy="review_allowed_shadow",
        gate_eligible_if_enabled=False,
        gate_blockers=["competition_not_allowed"],
        sanity_flags=["REVISAR"],
    )
    assert decision.eligible_for_rating_route is False
    assert "competition_not_allowed" in decision.blockers
    assert decision.warnings == []


def test_cli_policy_names_normalize():
    assert normalize_routing_policy("rating-replaces-fallback") == "rating_replaces_fallback"
    assert normalize_routing_policy("review-allowed-shadow") == "review_allowed_shadow"
