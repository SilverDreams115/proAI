"""Tests for the pure math helpers extracted from ModelTrainingArtifactsMixin.

These functions were previously inside a mixin and were exercised only
indirectly through the training service. Pulling them into a standalone
module lets us pin their behaviour with cheap, isolated tests so future
refactors can refactor confidently.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.services import model_training_math as mtm


class TestPoissonPmf:
    def test_matches_closed_form(self) -> None:
        # PMF at the mean of a Poisson(1) is 1/e.
        assert mtm.poisson_pmf(1.0, 1) == pytest.approx(math.exp(-1.0), rel=1e-9)

    def test_zero_lambda_is_a_point_mass_at_zero(self) -> None:
        assert mtm.poisson_pmf(0.0, 0) == 1.0
        assert mtm.poisson_pmf(0.0, 3) == 0.0

    def test_distribution_sums_to_one_over_truncated_support(self) -> None:
        # Across the same 0..8 grid the trainer uses, mass should be ~1.
        total = sum(mtm.poisson_pmf(1.4, k) for k in range(20))
        assert total == pytest.approx(1.0, abs=1e-9)


class TestDixonColesRho:
    def test_neutral_draw_rate_returns_near_zero_rho(self) -> None:
        assert mtm.dixon_coles_rho_from_draw_rate(0.26) == pytest.approx(0.0, abs=1e-9)

    def test_high_draw_rate_inflates_low_score_cells(self) -> None:
        assert mtm.dixon_coles_rho_from_draw_rate(0.40) > 0.0

    def test_clamped_to_paper_bounds(self) -> None:
        assert -0.2 <= mtm.dixon_coles_rho_from_draw_rate(0.0) <= 0.2
        assert -0.2 <= mtm.dixon_coles_rho_from_draw_rate(1.0) <= 0.2


class TestDixonColesTau:
    def test_tau_is_one_outside_low_score_cells(self) -> None:
        assert mtm.dixon_coles_tau(3, 2, 1.5, 1.2, 0.1) == 1.0
        assert mtm.dixon_coles_tau(2, 4, 1.5, 1.2, 0.1) == 1.0

    def test_zero_zero_cell_dampens_with_positive_rho(self) -> None:
        rho = 0.1
        tau = mtm.dixon_coles_tau(0, 0, 1.5, 1.2, rho)
        assert tau < 1.0

    def test_one_one_cell_dampens_with_positive_rho(self) -> None:
        tau = mtm.dixon_coles_tau(1, 1, 1.5, 1.2, 0.1)
        assert tau == pytest.approx(0.9)


class TestDixonColesOutcome:
    def test_probabilities_sum_to_one(self) -> None:
        p_h, p_d, p_a = mtm.dixon_coles_outcome(1.4, 1.1, 0.05)
        assert (p_h + p_d + p_a) == pytest.approx(1.0, abs=1e-9)

    def test_home_advantage_favours_home(self) -> None:
        p_h, _, p_a = mtm.dixon_coles_outcome(1.8, 1.0, 0.0)
        assert p_h > p_a

    def test_symmetric_lambdas_produce_symmetric_win_probs(self) -> None:
        p_h, _, p_a = mtm.dixon_coles_outcome(1.3, 1.3, 0.0)
        assert p_h == pytest.approx(p_a, abs=1e-9)

    def test_zero_lambdas_concentrate_mass_on_zero_zero_draw(self) -> None:
        # poisson_pmf(0,0)=1 and 0 elsewhere, so the only surviving cell
        # is the (0,0) draw. This is the degenerate but well-defined case.
        p_h, p_d, p_a = mtm.dixon_coles_outcome(0.0, 0.0, 0.0)
        assert p_d == pytest.approx(1.0, abs=1e-9)
        assert p_h == 0.0
        assert p_a == 0.0


class TestBrierScore:
    def test_perfect_call_scores_zero(self) -> None:
        assert mtm.brier_score([1.0, 0.0, 0.0], 0) == 0.0

    def test_uniform_three_class_distribution(self) -> None:
        # Brier(1/3,1/3,1/3 | actual=0) = (2/3)^2 + (1/3)^2 + (1/3)^2 = 6/9.
        assert mtm.brier_score([1 / 3, 1 / 3, 1 / 3], 0) == pytest.approx(6 / 9, rel=1e-9)


class TestLogLoss:
    def test_perfect_call_scores_near_zero(self) -> None:
        # Clipped at 1-1e-6 so we expect -log(1 - 1e-6) ~ 1e-6.
        assert mtm.log_loss([1.0, 0.0, 0.0], 0) == pytest.approx(-math.log(1 - 1e-6), abs=1e-9)

    def test_clips_zero_probability_to_avoid_infinity(self) -> None:
        # If we said "impossible" and the impossible happened, the loss
        # should saturate at -log(1e-6) instead of blowing up to inf.
        assert math.isfinite(mtm.log_loss([1.0, 0.0, 0.0], 1))


class TestTimeDecayWeights:
    def test_empty_input_returns_empty(self) -> None:
        assert mtm.time_decay_weights([]) == []

    def test_most_recent_match_has_weight_one(self) -> None:
        now = datetime(2026, 5, 29, tzinfo=timezone.utc)
        weights = mtm.time_decay_weights([now - timedelta(days=400), now])
        assert weights[-1] == pytest.approx(1.0, abs=1e-9)
        assert weights[0] < weights[-1]

    def test_decay_after_one_time_constant_is_e_inverse(self) -> None:
        # The implementation uses `exp(-age/tau)`, so at age=tau the weight
        # is 1/e ≈ 0.368, not 0.5. The constant is named `half_life_days`
        # by historical accident — kept as-is to avoid invalidating stored
        # artifacts that replay with the original decay curve.
        now = datetime(2026, 5, 29, tzinfo=timezone.utc)
        weights = mtm.time_decay_weights(
            [now - timedelta(days=365), now], half_life_days=365.0
        )
        assert weights[0] == pytest.approx(math.exp(-1.0), abs=1e-6)


class TestSafeRate:
    def test_zero_denominator_returns_zero(self) -> None:
        assert mtm.safe_rate(5, 0) == 0.0

    def test_negative_denominator_returns_zero(self) -> None:
        assert mtm.safe_rate(5, -1) == 0.0

    def test_none_inputs_coerce_to_zero(self) -> None:
        assert mtm.safe_rate(None, 10) == 0.0
        assert mtm.safe_rate(5, None) == 0.0

    def test_normal_division(self) -> None:
        assert mtm.safe_rate(3, 4) == 0.75
