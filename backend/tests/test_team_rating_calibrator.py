"""R5.3: team-rating calibrator candidate metadata and pure helpers."""

from __future__ import annotations

import pytest

from app.domain.team_rating_calibrator import apply_temperature_scaling
from app.domain.team_rating_calibrator import get_team_rating_calibrator_candidate
from app.domain.team_rating_calibrator import is_calibrator_candidate_compatible
from app.domain.team_rating_calibrator import TEAM_RATING_CALIBRATOR_CANDIDATES


def test_temperature_scaling_softens_distribution_when_temperature_gt_one():
    original = {"home": 0.8, "draw": 0.15, "away": 0.05}
    scaled = apply_temperature_scaling(original, 2.22)
    assert scaled["home"] < original["home"]
    assert scaled["away"] > original["away"]
    assert scaled["draw"] > original["draw"]


def test_temperature_scaling_output_sums_to_one_and_input_not_mutated():
    original = {"1": 0.7, "X": 0.2, "2": 0.1}
    before = dict(original)
    scaled = apply_temperature_scaling(original, 2.22)
    assert original == before
    assert set(scaled) == {"1", "X", "2"}
    assert sum(scaled.values()) == pytest.approx(1.0)


def test_temperature_scaling_handles_zero_with_safe_epsilon():
    scaled = apply_temperature_scaling({"home": 1.0, "draw": 0.0, "away": 0.0}, 2.22)
    assert sum(scaled.values()) == pytest.approx(1.0)
    assert scaled["draw"] > 0
    assert scaled["away"] > 0


def test_candidate_metadata_exists_and_is_not_productive():
    candidate = get_team_rating_calibrator_candidate(
        "international_friendlies_temperature_v1"
    )
    assert TEAM_RATING_CALIBRATOR_CANDIDATES[candidate.candidate_id] == candidate
    assert candidate.competition == "International Friendlies"
    assert candidate.subset == "both_medium_plus_only"
    assert candidate.temperature == 2.22
    assert candidate.routing_policy == "rating_replaces_fallback"
    assert candidate.productive_available is False
    assert candidate.test_rows == 161
    assert candidate.calibrated_brier == 0.6347
    assert candidate.baseline_brier == 0.7216
    assert candidate.calibrated_logloss == 1.0718
    assert candidate.baseline_logloss == 1.3125
    assert candidate.calibrated_ece == 0.1074
    assert candidate.baseline_ece == 0.2346


def test_candidate_compatible_for_international_friendlies():
    candidate = get_team_rating_calibrator_candidate(
        "international_friendlies_temperature_v1"
    )
    compatible, blockers = is_calibrator_candidate_compatible(
        candidate=candidate,
        competition_name="International Friendlies",
        subset="both_medium_plus_only",
        routing_policy="rating_replaces_fallback",
        min_test_rows=150,
    )
    assert compatible is True
    assert blockers == []


def test_candidate_incompatible_for_other_competitions():
    candidate = get_team_rating_calibrator_candidate(
        "international_friendlies_temperature_v1"
    )
    for competition in ("Copa Libertadores", "Brasileirao"):
        compatible, blockers = is_calibrator_candidate_compatible(
            candidate=candidate,
            competition_name=competition,
            subset="both_medium_plus_only",
            routing_policy="rating_replaces_fallback",
            min_test_rows=150,
        )
        assert compatible is False
        assert "competition_mismatch" in blockers


def test_candidate_incompatible_when_min_test_rows_exceeds_candidate_rows():
    candidate = get_team_rating_calibrator_candidate(
        "international_friendlies_temperature_v1"
    )
    compatible, blockers = is_calibrator_candidate_compatible(
        candidate=candidate,
        competition_name="International Friendlies",
        subset="both_medium_plus_only",
        routing_policy="rating_replaces_fallback",
        min_test_rows=162,
    )
    assert compatible is False
    assert "insufficient_test_rows" in blockers


def test_unknown_candidate_raises():
    with pytest.raises(ValueError):
        get_team_rating_calibrator_candidate("missing")
