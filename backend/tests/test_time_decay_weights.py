"""Tests for the time-decay weight helper (F7.1).

Time-decay weights make XGBoost care more about recent matches than
about seasons-old data. The helper anchors on the latest match in the
training set rather than on wall clock, so a backfill run today and a
backfill run a month from now both produce the same weights for the
same historical sample (deterministic training).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.services.model_training_artifacts import ModelTrainingArtifactsMixin


_weights = ModelTrainingArtifactsMixin._time_decay_weights


def test_empty_input_returns_empty_list() -> None:
    """No matches → no weights. The trainer code passes None to DMatrix
    when the list is empty so XGBoost falls back to uniform weighting."""
    assert _weights([]) == []


def test_most_recent_match_has_weight_one() -> None:
    """The latest match anchors the curve, so its weight is exactly 1.
    Anchoring on the most-recent timestamp (not `now`) keeps training
    deterministic across re-runs."""
    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    one_year_ago = now - timedelta(days=365)
    weights = _weights([one_year_ago, now])
    assert weights[1] == 1.0


def test_one_half_life_ago_is_half_weight() -> None:
    """With half_life_days=365, a match exactly one year before the most
    recent must carry exp(-1) of the weight — by construction. We pin the
    constant so future regressions to the formula are caught."""
    anchor = datetime(2026, 5, 26, tzinfo=timezone.utc)
    one_year_before = anchor - timedelta(days=365)
    weights = _weights([one_year_before, anchor])
    assert math.isclose(weights[0], math.exp(-1.0), rel_tol=1e-9)


def test_custom_half_life_changes_decay_speed() -> None:
    """Operators can pass a custom half-life. Confirm a shorter half-life
    drops the older sample's weight faster — that's the lever we'd use
    if backtesting shows the season constant is too lenient."""
    anchor = datetime(2026, 5, 26, tzinfo=timezone.utc)
    one_year_before = anchor - timedelta(days=365)
    standard = _weights([one_year_before, anchor])
    shorter = _weights([one_year_before, anchor], half_life_days=180.0)
    assert shorter[0] < standard[0]


def test_anchor_is_max_not_min() -> None:
    """If the dataset comes in unsorted, the helper still anchors on
    the latest match — no caller-side sort required. This matters because
    `_build_training_dataset` iterates the SQL result set in match
    iteration order, which is not guaranteed chronological."""
    early = datetime(2024, 1, 1, tzinfo=timezone.utc)
    late = datetime(2026, 5, 26, tzinfo=timezone.utc)
    unsorted = [late, early]
    weights = _weights(unsorted)
    # `late` is the anchor → its weight is 1.
    assert weights[0] == 1.0
    # `early` is ~2.4 years before → weight < exp(-1).
    assert weights[1] < math.exp(-1.0)
