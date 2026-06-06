"""Unit tests for the PSI drift detector (Fase 2.4)."""
from __future__ import annotations

import math
import random

from app.services.drift import compute_psi, train_drift_baseline


def test_psi_is_zero_for_identical_distributions() -> None:
    """PSI must be zero (within numerical noise) when the sample matches the
    baseline exactly."""
    baseline_rows = [{"x": float(i)} for i in range(100)]
    baseline = train_drift_baseline(baseline_rows, ["x"])["x"]
    sample = [float(i) for i in range(100)]
    psi = compute_psi(sample, baseline)
    assert math.isclose(psi, 0.0, abs_tol=1e-3), f"PSI for identical distros must be ~0, got {psi}"


def test_psi_grows_when_distribution_shifts() -> None:
    """Shifting the entire sample up by a large constant must produce a PSI
    above the >=0.25 'significant drift' threshold from the docs."""
    rng = random.Random(0)
    baseline_rows = [{"x": rng.gauss(0.0, 1.0)} for _ in range(2000)]
    baseline = train_drift_baseline(baseline_rows, ["x"])["x"]
    # Shifted by 2 standard deviations.
    sample = [rng.gauss(2.0, 1.0) for _ in range(2000)]
    psi = compute_psi(sample, baseline)
    assert psi > 0.25, f"PSI for 2-sigma shift should exceed 0.25, got {psi}"


def test_psi_low_for_slightly_perturbed_distribution() -> None:
    """A small noise perturbation should fall under the 0.10 'no meaningful
    change' threshold."""
    rng = random.Random(7)
    baseline_rows = [{"x": rng.gauss(0.0, 1.0)} for _ in range(2000)]
    baseline = train_drift_baseline(baseline_rows, ["x"])["x"]
    sample = [rng.gauss(0.05, 1.0) for _ in range(2000)]
    psi = compute_psi(sample, baseline)
    assert psi < 0.10, f"PSI for tiny perturbation should be <0.10, got {psi}"


def test_psi_zero_for_constant_feature() -> None:
    """If a feature is constant in the baseline, drift cannot be measured.
    Implementation returns 0 to avoid spurious alerts."""
    baseline_rows = [{"x": 1.0} for _ in range(50)]
    baseline = train_drift_baseline(baseline_rows, ["x"])["x"]
    sample = [1.0 for _ in range(50)]
    psi = compute_psi(sample, baseline)
    assert psi == 0.0


def test_baseline_skips_features_missing_in_rows() -> None:
    """Asked for a feature that is not in any row -> empty baseline, PSI 0."""
    baseline = train_drift_baseline([{"x": 1.0}, {"x": 2.0}], ["missing"])
    assert baseline["missing"] == {"bins": [], "freq": []}
    assert compute_psi([0.5, 1.5], baseline["missing"]) == 0.0


def test_psi_handles_empty_sample() -> None:
    baseline_rows = [{"x": float(i)} for i in range(20)]
    baseline = train_drift_baseline(baseline_rows, ["x"])["x"]
    assert compute_psi([], baseline) == 0.0
