"""Tests for the per-league stacking meta-learner (Fase 4.1)."""
from __future__ import annotations

import math
import random

from app.services.stacking import (
    apply_stacker,
    build_feature_vector,
    cross_entropy,
    softmax,
    train_stacker,
)


def test_softmax_returns_normalized_distribution() -> None:
    out = softmax([1.0, 2.0, 0.5])
    assert math.isclose(sum(out), 1.0, abs_tol=1e-9)
    assert all(0.0 < value < 1.0 for value in out)


def test_softmax_handles_extreme_logits_without_overflow() -> None:
    out = softmax([1000.0, 999.0, 0.0])
    # The two huge logits dominate; the small one collapses to ~0.
    assert math.isclose(out[2], 0.0, abs_tol=1e-9)
    assert out[0] > out[1] > out[2]


def test_train_returns_empty_for_undersized_sample() -> None:
    """Less than n_classes + 1 samples: do not even attempt to fit."""
    artifact = train_stacker([], n_classes=3)
    assert artifact == {}
    artifact = train_stacker(
        [([[0.5, 0.3, 0.2], [0.4, 0.4, 0.2]], 0)],
        n_classes=3,
    )
    assert artifact == {}


def test_apply_returns_none_when_artifact_is_empty() -> None:
    """An empty stacker artifact means we never trained — caller falls
    back to the base model. apply must surface that signal."""
    assert apply_stacker([[0.5, 0.3, 0.2], [0.4, 0.4, 0.2]], {}) is None


def test_apply_returns_none_for_wrong_feature_count() -> None:
    """Saved with 2 base models but called with 3 — refuse rather than
    silently slicing features."""
    artifact = train_stacker(
        [
            ([[0.5, 0.3, 0.2], [0.4, 0.4, 0.2]], 0),
            ([[0.3, 0.4, 0.3], [0.3, 0.4, 0.3]], 1),
            ([[0.2, 0.3, 0.5], [0.2, 0.3, 0.5]], 2),
            ([[0.6, 0.2, 0.2], [0.5, 0.3, 0.2]], 0),
        ]
    )
    assert artifact != {}
    extra_base = [[0.5, 0.3, 0.2], [0.4, 0.4, 0.2], [0.3, 0.3, 0.4]]
    assert apply_stacker(extra_base, artifact) is None


def test_stacker_learns_to_trust_the_correct_base_model() -> None:
    """If base model A is consistently right and base model B is noise,
    the stacker must pull toward A's predictions on held-out samples."""
    rng = random.Random(13)
    samples = []
    # Train: 30 samples where class index equals which class B has highest
    # (B is right). A is always uniform [0.34, 0.33, 0.33].
    for _ in range(60):
        correct_class = rng.randint(0, 2)
        good = [0.10, 0.10, 0.10]
        good[correct_class] = 0.80
        bad = [0.40, 0.30, 0.30]  # noise that always picks class 0
        samples.append(([bad, good], correct_class))

    artifact = train_stacker(samples, iterations=600, learning_rate=0.1)
    assert artifact != {}

    # Held-out evaluation: stacker should beat the noisy model.
    held_out = [
        ([[0.40, 0.30, 0.30], [0.10, 0.80, 0.10]], 1),
        ([[0.40, 0.30, 0.30], [0.10, 0.10, 0.80]], 2),
        ([[0.40, 0.30, 0.30], [0.80, 0.10, 0.10]], 0),
    ]
    stacker_loss = 0.0
    noisy_loss = 0.0
    for base_predictions, target in held_out:
        stacked = apply_stacker(base_predictions, artifact)
        assert stacked is not None
        stacker_loss += cross_entropy(stacked, target)
        noisy_loss += cross_entropy(base_predictions[0], target)
    # The stacker must clearly outperform the noisy base on average.
    assert stacker_loss < noisy_loss


def test_build_feature_vector_includes_bias_term() -> None:
    """Last entry must be 1.0 — the bias trick keeps the LR linear in
    `[probs..., 1]` instead of needing a separate intercept."""
    features = build_feature_vector([[0.5, 0.3, 0.2], [0.4, 0.4, 0.2]])
    assert features[-1] == 1.0
    assert len(features) == 7


def test_stacker_output_sums_to_one() -> None:
    artifact = train_stacker(
        [
            ([[0.55, 0.25, 0.20], [0.50, 0.30, 0.20]], 0),
            ([[0.20, 0.30, 0.50], [0.25, 0.30, 0.45]], 2),
            ([[0.30, 0.40, 0.30], [0.35, 0.35, 0.30]], 1),
            ([[0.40, 0.35, 0.25], [0.42, 0.32, 0.26]], 0),
            ([[0.30, 0.30, 0.40], [0.32, 0.28, 0.40]], 2),
        ]
    )
    stacked = apply_stacker([[0.5, 0.3, 0.2], [0.4, 0.4, 0.2]], artifact)
    assert stacked is not None
    assert math.isclose(sum(stacked), 1.0, abs_tol=1e-9)
    assert all(0.0 < value < 1.0 for value in stacked)
