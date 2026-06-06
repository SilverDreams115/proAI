"""Tests for the pure metric helpers in model_training_metrics.

Walk-forward aggregation has subtle edge cases (no confident picks,
saturated hit rate, threshold mis-orderings) that used to require a
fully wired training service to exercise. Now they're table-driven
unit tests that run in milliseconds.
"""

from __future__ import annotations

import pytest

from app.services.model_training_metrics import (
    drift_severity,
    summarize_walk_forward,
)


READY_THRESHOLDS = {
    "hit_rate": 0.55,
    "brier_score_max": 0.65,
    "confident_hit_rate": 0.62,
    "min_confident_picks": 30,
}


class TestDriftSeverity:
    def test_stable_band(self) -> None:
        assert drift_severity(0.0) == "stable"
        assert drift_severity(0.099) == "stable"

    def test_moderate_band(self) -> None:
        assert drift_severity(0.10) == "moderate"
        assert drift_severity(0.249) == "moderate"

    def test_significant_band(self) -> None:
        assert drift_severity(0.25) == "significant"
        assert drift_severity(1.0) == "significant"


def _summarize(
    *,
    evaluated: int,
    hits: int,
    confident_picks: int,
    confident_hits: int,
    brier_total: float = 0.0,
    log_loss_total: float = 0.0,
):
    return summarize_walk_forward(
        selected_model_name="test_model",
        matches_considered=evaluated,
        evaluated=evaluated,
        hits=hits,
        brier_total=brier_total,
        log_loss_total=log_loss_total,
        confident_picks=confident_picks,
        confident_hits=confident_hits,
        min_training_matches=6,
        confidence_threshold=0.5,
        thresholds=READY_THRESHOLDS,
    )


class TestSummarizeWalkForward:
    def test_no_confident_picks_short_circuits_to_insufficient(self) -> None:
        report = _summarize(evaluated=10, hits=6, confident_picks=0, confident_hits=0)
        assert report["verdict"] == "insufficient_confident_samples"
        assert report["confident_pick_hit_rate"] == 0.0
        assert report["ready_for_live_picks"] is False

    def test_too_few_confident_picks_marks_insufficient(self) -> None:
        report = _summarize(evaluated=100, hits=60, confident_picks=20, confident_hits=18)
        assert report["verdict"] == "insufficient_confident_samples"

    def test_all_thresholds_clear_ready(self) -> None:
        report = _summarize(
            evaluated=100,
            hits=60,
            confident_picks=40,
            confident_hits=28,
            brier_total=50.0,
            log_loss_total=80.0,
        )
        assert report["ready_for_live_picks"] is True
        assert report["verdict"] == "ready"
        assert report["hit_rate"] == 0.6
        assert report["brier_score"] == 0.5

    def test_hit_rate_just_under_threshold_marks_not_ready(self) -> None:
        report = _summarize(
            evaluated=100,
            hits=54,
            confident_picks=40,
            confident_hits=28,
            brier_total=50.0,
        )
        assert report["ready_for_live_picks"] is False
        assert report["verdict"] == "not_ready"

    def test_brier_too_high_marks_not_ready(self) -> None:
        report = _summarize(
            evaluated=100,
            hits=60,
            confident_picks=40,
            confident_hits=28,
            brier_total=70.0,
        )
        assert report["ready_for_live_picks"] is False
        assert report["verdict"] == "not_ready"

    def test_thresholds_round_trip(self) -> None:
        report = _summarize(evaluated=10, hits=6, confident_picks=5, confident_hits=4)
        assert report["thresholds"] == READY_THRESHOLDS
        # Defensive copy: mutating the result must not bleed into our literal.
        report["thresholds"]["hit_rate"] = 0.99
        assert READY_THRESHOLDS["hit_rate"] == 0.55

    def test_division_by_zero_protected_when_evaluated_is_zero(self) -> None:
        # The caller is supposed to short-circuit before invoking summarize
        # when nothing was evaluated, but if they don't, we should at
        # least raise a clear error rather than return garbage.
        with pytest.raises(ZeroDivisionError):
            _summarize(evaluated=0, hits=0, confident_picks=0, confident_hits=0)
