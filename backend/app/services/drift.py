"""Population Stability Index (PSI) for monitoring feature drift.

PSI compares two distributions over the same set of bins:

    PSI = sum_i (q_i - p_i) * ln(q_i / p_i)

Conventions used downstream:
- PSI < 0.10  : no meaningful change.
- 0.10 - 0.25 : moderate drift, investigate.
- >= 0.25     : significant drift, retrain.

We persist a fixed bin layout (quantile cuts) per feature in the model
artifact so production-time samples are bucketed exactly the same way the
training distribution was. The `train_drift_baseline` function returns a
plain dict ready to round-trip through the artifact JSON.
"""
from __future__ import annotations

import math
from typing import Any


_EPSILON = 1e-6
"""Floor applied to bin proportions before computing log ratios."""

_BIN_COUNT = 10


def train_drift_baseline(rows: list[dict[str, float]], feature_names: list[str]) -> dict[str, Any]:
    """Compute quantile bin edges and reference frequencies per feature.

    Args:
        rows: training rows as feature-name -> value dicts. Empty rows are
            skipped silently.
        feature_names: list of feature names to monitor. Anything else in
            `rows` is ignored.

    Returns:
        `{feature_name: {"bins": [edge_0, ..., edge_n], "freq": [p_0, ..., p_n-1]}}`.
        Features with constant values get a single-bin layout (PSI is then
        always 0 for that feature).
    """
    baseline: dict[str, Any] = {}
    for feature_name in feature_names:
        values = [float(row[feature_name]) for row in rows if feature_name in row]
        baseline[feature_name] = _baseline_for_feature(values)
    return baseline


def compute_psi(
    sample_values: list[float],
    baseline: dict[str, Any],
) -> float:
    """Compare a new sample of one feature against its baseline.

    Args:
        sample_values: observed values to score.
        baseline: structure produced by `train_drift_baseline` for this
            feature.

    Returns:
        The PSI value. Returns 0.0 when sample or baseline is empty.
    """
    if not sample_values:
        return 0.0
    bin_edges = baseline.get("bins") or []
    reference_freq = baseline.get("freq") or []
    if not bin_edges or not reference_freq:
        return 0.0
    sample_freq = _bucket_frequencies(sample_values, bin_edges)
    psi = 0.0
    for ref, sample in zip(reference_freq, sample_freq, strict=False):
        p = max(float(ref), _EPSILON)
        q = max(float(sample), _EPSILON)
        psi += (q - p) * math.log(q / p)
    return psi


def _baseline_for_feature(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"bins": [], "freq": []}
    sorted_values = sorted(values)
    minimum = sorted_values[0]
    maximum = sorted_values[-1]
    if math.isclose(minimum, maximum):
        # Constant feature: store one bin so PSI is trivially 0 at runtime.
        return {"bins": [minimum, maximum], "freq": [1.0]}
    edges = _quantile_edges(sorted_values, _BIN_COUNT)
    frequencies = _bucket_frequencies(values, edges)
    return {"bins": edges, "freq": frequencies}


def _quantile_edges(sorted_values: list[float], bin_count: int) -> list[float]:
    """Build approximately equal-frequency bin edges.

    Returns `bin_count + 1` floats. Adjacent duplicate edges are kept (which
    creates an empty bin at runtime) so the index alignment matches between
    the training baseline and any future sample."""
    n = len(sorted_values)
    if bin_count <= 0 or n == 0:
        return []
    edges = [sorted_values[0]]
    for i in range(1, bin_count):
        position = int(round(i * n / bin_count))
        position = min(max(position, 0), n - 1)
        edges.append(sorted_values[position])
    edges.append(sorted_values[-1])
    return edges


def _bucket_frequencies(values: list[float], bin_edges: list[float]) -> list[float]:
    """Distribute `values` across `len(bin_edges) - 1` bins, returning
    proportions that sum to 1 (subject to floating point)."""
    if len(bin_edges) < 2 or not values:
        return []
    bin_count = len(bin_edges) - 1
    counts = [0] * bin_count
    for raw_value in values:
        index = _bucket_index(float(raw_value), bin_edges)
        counts[index] += 1
    total = sum(counts) or 1
    return [count / total for count in counts]


def _bucket_index(value: float, bin_edges: list[float]) -> int:
    """Find the bin a value falls in. Values below the lowest edge go to
    bin 0; values at or above the top edge go to the last bin."""
    bin_count = len(bin_edges) - 1
    for index in range(bin_count):
        upper = bin_edges[index + 1]
        if value <= upper:
            return index
    return bin_count - 1
