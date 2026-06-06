"""Per-league stacking meta-learner over base scorers (Fase 4.1).

Each base scorer (Dixon-Coles heuristic blend, XGBoost) emits a three-way
probability vector. The stacker treats those vectors as features and
learns the optimal convex combination per league via multinomial
logistic regression trained on out-of-fold predictions.

Why per-league: a model that is sharp in Premier League may be poorly
calibrated in Brasileirao. Stacking weights learned league-wide would
average that out instead of letting each league pick its best blend.

Why pure Python: we avoid scikit-learn (project rule, F2.1). The problem
is tiny — 6 features (two 3-class vectors), tens to hundreds of OOF
samples per league — so a hand-rolled multinomial logistic regression
with L-BFGS or plain gradient descent finishes in tens of milliseconds.

Output of `train_stacker` is a JSON-serializable dict carrying the
coefficient matrix per league. `apply_stacker` consumes the same dict
and returns the calibrated three-way distribution.
"""
from __future__ import annotations

import math
from typing import Any


_PROB_FLOOR = 1e-6
_DEFAULT_ITERATIONS = 400
_DEFAULT_LEARNING_RATE = 0.05
_L2_REG = 1e-3


def softmax(logits: list[float]) -> list[float]:
    """Numerically stable softmax over a logit vector."""
    if not logits:
        return []
    high = max(logits)
    exps = [math.exp(value - high) for value in logits]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(logits) for _ in logits]
    return [value / total for value in exps]


def cross_entropy(probabilities: list[float], target_index: int) -> float:
    floored = max(probabilities[target_index], _PROB_FLOOR)
    return -math.log(floored)


def build_feature_vector(base_predictions: list[list[float]]) -> list[float]:
    """Flatten the base predictions (one list per base model) into a single
    feature vector. The bias term is appended last."""
    flattened: list[float] = []
    for prediction in base_predictions:
        flattened.extend(float(value) for value in prediction)
    flattened.append(1.0)  # bias
    return flattened


def train_stacker(
    samples: list[tuple[list[list[float]], int]],
    *,
    n_classes: int = 3,
    iterations: int = _DEFAULT_ITERATIONS,
    learning_rate: float = _DEFAULT_LEARNING_RATE,
    l2: float = _L2_REG,
) -> dict[str, Any]:
    """Fit a multinomial logistic regression on stacked base predictions.

    Args:
        samples: list of `(base_predictions, target_class_index)`.
            `base_predictions` is `[[home, draw, away], [home, draw, away], ...]`
            with one row per base model. Order must be stable; the same
            order is required at apply time.
        n_classes: number of output classes (3 for 1/X/2).
        iterations: gradient descent steps.
        learning_rate: step size.
        l2: ridge penalty strength.

    Returns:
        `{"weights": [[...], [...], [...]], "n_features": int, "n_classes": int}`.
        Returns an empty dict when the sample is too small to fit safely
        (`len(samples) < n_classes + 1`).
    """
    if not samples or len(samples) < n_classes + 1:
        return {}
    n_features = len(build_feature_vector(samples[0][0]))
    weights = [[0.0] * n_features for _ in range(n_classes)]
    n_samples = len(samples)

    for _ in range(iterations):
        # Accumulated gradient for the whole batch (small N, stable updates).
        gradient = [[0.0] * n_features for _ in range(n_classes)]
        for base_predictions, target in samples:
            features = build_feature_vector(base_predictions)
            logits = [
                sum(w * x for w, x in zip(class_weights, features, strict=True))
                for class_weights in weights
            ]
            probabilities = softmax(logits)
            for class_index in range(n_classes):
                error = probabilities[class_index] - (1.0 if class_index == target else 0.0)
                row = gradient[class_index]
                for feature_index, feature_value in enumerate(features):
                    row[feature_index] += error * feature_value
        # Apply averaged gradient with L2 shrinkage.
        for class_index in range(n_classes):
            for feature_index in range(n_features):
                grad = gradient[class_index][feature_index] / n_samples
                grad += l2 * weights[class_index][feature_index]
                weights[class_index][feature_index] -= learning_rate * grad

    return {"weights": weights, "n_features": n_features, "n_classes": n_classes}


def apply_stacker(
    base_predictions: list[list[float]],
    stacker_artifact: dict[str, Any],
) -> list[float] | None:
    """Map a fresh set of base predictions through the trained stacker.

    Returns the calibrated probability vector, or None when the artifact
    is empty/malformed (caller falls back to the best base model)."""
    weights = stacker_artifact.get("weights")
    expected_features = stacker_artifact.get("n_features")
    if not isinstance(weights, list) or not isinstance(expected_features, int):
        return None
    features = build_feature_vector(base_predictions)
    if len(features) != expected_features:
        return None
    try:
        logits = [
            sum(float(w) * x for w, x in zip(class_weights, features, strict=True))
            for class_weights in weights
        ]
    except (TypeError, ValueError):
        return None
    return softmax(logits)
