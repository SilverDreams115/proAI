"""Tests for the Poisson Binomial coverage math (Fase 5.1)."""
from __future__ import annotations

import math

from app.services.coverage import (
    expected_successes,
    poisson_binomial_pmf,
    prob_at_least,
)


def test_empty_probabilities_yield_trivial_pmf() -> None:
    """Zero trials = one outcome (zero successes), mass 1."""
    pmf = poisson_binomial_pmf([])
    assert pmf == [1.0]
    assert prob_at_least([], 0) == 1.0
    assert prob_at_least([], 1) == 0.0


def test_uniform_probability_matches_binomial_distribution() -> None:
    """If all p are equal, Poisson Binomial collapses to Binomial.
    Verify against the closed form for n=5, p=0.4."""
    probs = [0.4] * 5
    pmf = poisson_binomial_pmf(probs)
    expected = [
        math.comb(5, k) * (0.4 ** k) * (0.6 ** (5 - k))
        for k in range(6)
    ]
    for k, (actual, want) in enumerate(zip(pmf, expected, strict=True)):
        assert math.isclose(actual, want, abs_tol=1e-9), f"k={k} got {actual}, expected {want}"


def test_pmf_sums_to_one() -> None:
    """A probability distribution must sum to 1 regardless of inputs."""
    pmf = poisson_binomial_pmf([0.1, 0.4, 0.9, 0.55, 0.7])
    assert math.isclose(sum(pmf), 1.0, abs_tol=1e-9)


def test_prob_at_least_complements_pmf() -> None:
    """P(>=k) must equal sum of pmf[k:] for arbitrary inputs."""
    probs = [0.30, 0.50, 0.65, 0.40, 0.55, 0.20]
    pmf = poisson_binomial_pmf(probs)
    for k in range(len(probs) + 2):
        expected = sum(pmf[k:]) if 0 <= k <= len(probs) else (1.0 if k <= 0 else 0.0)
        assert math.isclose(prob_at_least(probs, k), expected, abs_tol=1e-9)


def test_prob_at_least_zero_is_always_one() -> None:
    assert prob_at_least([0.1, 0.2, 0.3], 0) == 1.0
    assert prob_at_least([0.0, 0.0, 0.0], 0) == 1.0


def test_prob_at_least_above_n_is_zero() -> None:
    assert prob_at_least([0.9, 0.9, 0.9], 4) == 0.0


def test_triple_certainty_pushes_pmf_to_the_right() -> None:
    """A trial with p=1.0 must add exactly one to every outcome bucket."""
    pmf_with_triple = poisson_binomial_pmf([0.5, 1.0])
    # Outcomes: 1 success (the 0.5 trial misses) or 2 successes (it hits).
    assert math.isclose(pmf_with_triple[0], 0.0, abs_tol=1e-9)
    assert math.isclose(pmf_with_triple[1], 0.5, abs_tol=1e-9)
    assert math.isclose(pmf_with_triple[2], 0.5, abs_tol=1e-9)


def test_clipping_keeps_probabilities_safe() -> None:
    """Out-of-range inputs (negative, >1) must not break the recurrence."""
    pmf = poisson_binomial_pmf([1.5, -0.3, 0.5])
    # 1.5 -> 1.0 (always hits), -0.3 -> 0.0 (never hits), 0.5 -> half-half.
    # So pmf[1] = 0.5, pmf[2] = 0.5, rest 0.
    assert math.isclose(sum(pmf), 1.0, abs_tol=1e-9)
    assert math.isclose(pmf[1], 0.5, abs_tol=1e-9)
    assert math.isclose(pmf[2], 0.5, abs_tol=1e-9)


def test_expected_successes_is_sum_of_probabilities() -> None:
    """Linearity of expectation holds even for non-identical Bernoulli."""
    assert math.isclose(expected_successes([0.1, 0.3, 0.6]), 1.0, abs_tol=1e-9)


def test_progol_scale_completes_quickly() -> None:
    """14-match slate must run in microseconds — guards against quadratic
    regressions in the recurrence."""
    import time

    probs = [0.4 + (i % 5) * 0.05 for i in range(14)]
    started = time.perf_counter()
    pmf = poisson_binomial_pmf(probs)
    elapsed = time.perf_counter() - started
    assert math.isclose(sum(pmf), 1.0, abs_tol=1e-9)
    assert elapsed < 0.01, f"slate-scale PMF must be near-instant, took {elapsed:.4f}s"
