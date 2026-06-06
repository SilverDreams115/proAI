"""Isotonic calibration via Pool Adjacent Violators (PAV).

Pure Python implementation. Used to calibrate per-league per-class
probability outputs of the heuristic blend so that the reported
confidence matches empirical hit rate.

Fitting:
    breakpoints = fit_pav([(raw_p, actual_one_hot), ...])

Applying:
    calibrated_p = apply_isotonic(breakpoints, raw_p)

Calibration curves serialize as plain Python lists of (x, y) pairs, ready
to round-trip through the model artifact JSON.
"""
from __future__ import annotations

from typing import Iterable


Breakpoint = tuple[float, float]
"""A single (x_threshold, y_calibrated) point in the step function."""


def fit_pav(samples: Iterable[tuple[float, float]]) -> list[Breakpoint]:
    """Fit isotonic regression with the Pool Adjacent Violators algorithm.

    Args:
        samples: iterable of (raw_probability, target) pairs where target is
            usually 0.0 or 1.0 (one-hot encoding of the actual class) but can
            also be a soft target in [0, 1].

    Returns:
        A list of (x, y) breakpoints sorted by x. `y` is monotone
        non-decreasing in x. Apply with `apply_isotonic`.

        Returns an empty list if the input is empty.
    """
    sorted_samples = sorted(samples, key=lambda pair: pair[0])
    if not sorted_samples:
        return []

    # Each pool tracks [x_right_edge, sum_of_targets, weight].
    # We start with one pool per sample (weight=1).
    pools: list[list[float]] = [[float(x), float(y), 1.0] for x, y in sorted_samples]

    i = 0
    while i + 1 < len(pools):
        avg_current = pools[i][1] / pools[i][2]
        avg_next = pools[i + 1][1] / pools[i + 1][2]
        if avg_current > avg_next:
            # Merge into the right pool (keeps the rightmost x).
            pools[i] = [
                pools[i + 1][0],
                pools[i][1] + pools[i + 1][1],
                pools[i][2] + pools[i + 1][2],
            ]
            del pools[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1

    return [(pool[0], pool[1] / pool[2]) for pool in pools]


def apply_isotonic(breakpoints: list[Breakpoint], x: float) -> float:
    """Predict the calibrated probability for a raw input.

    Uses linear interpolation between the consecutive breakpoint pairs
    whose x covers the input, and clamps to the y-range of the curve
    outside that span.

    Args:
        breakpoints: output of `fit_pav`. Empty list means no calibration
            available — the caller should keep `x`.
        x: raw probability to calibrate.

    Returns:
        The calibrated probability, clamped to [0.0, 1.0]. Returns `x` when
        `breakpoints` is empty.
    """
    if not breakpoints:
        return _clamp_unit(x)
    if x <= breakpoints[0][0]:
        return _clamp_unit(breakpoints[0][1])
    if x >= breakpoints[-1][0]:
        return _clamp_unit(breakpoints[-1][1])

    # Find the interval containing x and interpolate linearly.
    for index in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[index]
        x1, y1 = breakpoints[index + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return _clamp_unit(y0)
            t = (x - x0) / (x1 - x0)
            return _clamp_unit(y0 + t * (y1 - y0))
    return _clamp_unit(breakpoints[-1][1])


def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
