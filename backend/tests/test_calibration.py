"""Unit tests for the PAV isotonic calibration module (Fase 1.2)."""
from __future__ import annotations

import math

import pytest

from app.services.calibration import apply_isotonic, fit_pav


def test_pav_is_monotone_non_decreasing() -> None:
    """Output curve must never go down as x increases — that is the PAV
    guarantee."""
    # Deliberate violation: target at x=0.6 < target at x=0.4. PAV must
    # merge these into a single pool.
    samples = [(0.1, 0.0), (0.2, 0.0), (0.4, 1.0), (0.6, 0.0), (0.8, 1.0), (0.9, 1.0)]
    breakpoints = fit_pav(samples)
    ys = [y for _, y in breakpoints]
    assert ys == sorted(ys), f"PAV must produce non-decreasing y: {ys}"


def test_pav_pools_pure_violation() -> None:
    """Two adjacent points with target 0 then 1 should each keep their own
    target. A violating pair (1 then 0) must collapse to 0.5."""
    breakpoints = fit_pav([(0.3, 1.0), (0.7, 0.0)])
    ys = [y for _, y in breakpoints]
    assert all(math.isclose(y, 0.5) for y in ys), f"violating pair must average to 0.5: {ys}"


def test_pav_perfectly_ordered_input_is_preserved() -> None:
    """Already-monotone input passes through unchanged."""
    samples = [(0.1, 0.0), (0.3, 0.0), (0.6, 1.0), (0.9, 1.0)]
    breakpoints = fit_pav(samples)
    assert len(breakpoints) == 4
    assert [round(y, 4) for _, y in breakpoints] == [0.0, 0.0, 1.0, 1.0]


def test_pav_handles_empty_input() -> None:
    assert fit_pav([]) == []


def test_apply_isotonic_returns_input_when_no_curve() -> None:
    """With no breakpoints the calibrator must be the identity (clamped)."""
    assert apply_isotonic([], 0.4) == 0.4
    assert apply_isotonic([], 1.5) == 1.0
    assert apply_isotonic([], -0.2) == 0.0


def test_apply_isotonic_clips_outside_curve_domain() -> None:
    """Below the smallest x: clamp to the smallest y. Above the largest x:
    clamp to the largest y."""
    breakpoints = [(0.2, 0.1), (0.5, 0.4), (0.9, 0.8)]
    assert apply_isotonic(breakpoints, 0.01) == 0.1
    assert apply_isotonic(breakpoints, 0.99) == 0.8


def test_apply_isotonic_linearly_interpolates_between_points() -> None:
    """Between (0.2, 0.1) and (0.6, 0.5), x=0.4 should map to 0.3."""
    breakpoints = [(0.2, 0.1), (0.6, 0.5)]
    assert math.isclose(apply_isotonic(breakpoints, 0.4), 0.3, abs_tol=1e-6)


def test_apply_isotonic_clamps_to_unit_interval() -> None:
    """Even if the curve overshoots [0, 1] due to soft targets, the apply
    step must clamp the output."""
    breakpoints = [(0.0, -0.1), (1.0, 1.4)]
    assert apply_isotonic(breakpoints, 0.5) == pytest.approx(0.65)  # interpolation OK
    assert apply_isotonic(breakpoints, 0.0) == 0.0  # clamped from -0.1
    assert apply_isotonic(breakpoints, 1.0) == 1.0  # clamped from 1.4


def test_pav_with_overconfident_predictor() -> None:
    """Realistic scenario: a model predicts 0.9 a lot but only hits ~70%.
    Isotonic must pull the high end down."""
    # 10 samples at p=0.9 with 7 hits, 10 at p=0.5 with 5 hits, 10 at p=0.2 with 2 hits.
    samples: list[tuple[float, float]] = []
    samples.extend((0.2, 1.0) for _ in range(2))
    samples.extend((0.2, 0.0) for _ in range(8))
    samples.extend((0.5, 1.0) for _ in range(5))
    samples.extend((0.5, 0.0) for _ in range(5))
    samples.extend((0.9, 1.0) for _ in range(7))
    samples.extend((0.9, 0.0) for _ in range(3))

    breakpoints = fit_pav(samples)
    # Apply at the three operating points the model emits.
    cal_low = apply_isotonic(breakpoints, 0.2)
    cal_mid = apply_isotonic(breakpoints, 0.5)
    cal_high = apply_isotonic(breakpoints, 0.9)

    # Calibrated probabilities should approximate empirical hit rates.
    assert math.isclose(cal_low, 0.2, abs_tol=0.05), f"low: {cal_low}"
    assert math.isclose(cal_mid, 0.5, abs_tol=0.05), f"mid: {cal_mid}"
    assert math.isclose(cal_high, 0.7, abs_tol=0.05), f"high: {cal_high}"
    # And the high end was pulled down (overconfidence corrected).
    assert cal_high < 0.9
