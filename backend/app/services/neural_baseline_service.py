"""Experimental neural baseline for Progol adaptive learning.

STATUS: EXPERIMENTAL — READ-ONLY SHADOW IN PRODUCTION PREDICTION RESPONSES.

This module implements a lightweight 2-hidden-layer MLP trained on the
adaptive dataset produced by ``AdaptiveDatasetService``.  It is intended
for offline research only:

  * It does NOT replace XGBoost / ELO / Poisson.
  * ``dry_run_train`` and ``train_offline`` do NOT write to the DB.
  * Candidate/active registry helpers may write non-production
    ``model_training_runs`` entries under neural-specific model names.
  * It can add read-only ``neural_shadow`` diagnostics to live prediction
    responses, but it does NOT replace probabilities, picks, or tickets.
  * It does NOT train when ``trainable_rows < config.min_rows``.
  * Every artifact it writes carries ``model_type = "neural_baseline_experimental"``
    and ``is_production = False``.

Implementation choice — pure numpy:
  The runtime already ships numpy (2.x) via the xgboost transitive
  dependency.  Adding PyTorch or scikit-learn just for an offline
  experiment would bloat the Docker image.  A 2-layer MLP trained with
  mini-batch gradient descent is mathematically equivalent for this
  dataset size and avoids any new install.

  If you later want to swap in PyTorch, replace ``_NumpyMLP`` with a
  ``torch.nn.Module`` and keep the ``NeuralBaselineModel`` wrapper.

Architecture:
  input (dynamic) → Dense 64 ReLU → Dense 32 ReLU → Dense 3 Softmax
  loss  = multi-class cross-entropy
  optim = vanilla SGD (no momentum for minimal complexity)

Feature set (13 fixed-width columns, pre-match safe):
  0  prob_home      (float, 0–1)
  1  prob_draw      (float, 0–1)
  2  prob_away      (float, 0–1)
  3  band_high      (0/1)
  4  band_medium    (0/1)
  5  band_low       (0/1)
  6  band_blocked   (0/1)
  7  wt_weekend     (0/1)
  8  wt_midweek     (0/1)
  9  has_block_reason (0/1)
  10 ticket_pick_1  (0/1)
  11 ticket_pick_X  (0/1)
  12 ticket_pick_2  (0/1)

Target labels:
  0 = home win  ("1")
  1 = draw      ("X")
  2 = away win  ("2")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.repositories.training_repository import TrainingRepository
from app.schemas.adaptive_dataset import AdaptiveDatasetRow
from app.schemas.prediction import MatchPredictionResponse, NeuralShadowInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = [
    "prob_home", "prob_draw", "prob_away",
    "band_high", "band_medium", "band_low", "band_blocked",
    "wt_weekend", "wt_midweek",
    "has_blocked_reason",
    "ticket_pick_1", "ticket_pick_X", "ticket_pick_2",
]
INPUT_DIM = len(FEATURE_NAMES)

RESULT_TO_IDX: dict[str, int] = {"1": 0, "X": 1, "2": 2}
IDX_TO_RESULT: dict[int, str] = {0: "1", 1: "X", 2: "2"}

_BANDS = ("high", "medium", "low", "blocked")
_WEEK_TYPES = ("weekend", "midweek")
NEURAL_CANDIDATE_MODEL_NAME = "neural_baseline_candidate"
NEURAL_ACTIVE_MODEL_NAME = "neural_baseline_active"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class NeuralBaselineConfig:
    hidden_dims: list[int] = field(default_factory=lambda: [64, 32])
    learning_rate: float = 0.01
    epochs: int = 150
    batch_size: int = 32
    min_rows: int = 20
    random_seed: int = 42
    model_type: str = "neural_baseline_experimental"
    is_production: bool = False


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

class NeuralDatasetBuilder:
    """Converts ``AdaptiveDatasetRow`` objects into numpy arrays.

    Rows are excluded when:
    - ``actual_result`` is not in {"1", "X", "2"}  (invalid / conflict)
    - ``prob_home`` / ``prob_draw`` / ``prob_away`` are all None
      (prediction was never made — slate_id=None case)
    """

    def build(
        self,
        rows: list[AdaptiveDatasetRow],
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """Return (X, y, feature_names).

        Raises ``ValueError`` if no valid rows remain after filtering.
        """
        X_list: list[list[float]] = []
        y_list: list[int] = []

        for row in rows:
            label = RESULT_TO_IDX.get(row.actual_result)
            if label is None:
                continue
            if row.prob_home is None and row.prob_draw is None and row.prob_away is None:
                continue

            X_list.append(self._encode(row))
            y_list.append(label)

        if not X_list:
            raise ValueError(
                "No valid rows to encode. "
                "All rows lacked a valid actual_result or had no prediction probabilities."
            )

        return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int64), FEATURE_NAMES

    @staticmethod
    def _encode(row: AdaptiveDatasetRow) -> list[float]:
        ph = row.prob_home or 1.0 / 3
        pd = row.prob_draw or 1.0 / 3
        pa = row.prob_away or 1.0 / 3

        band = row.confidence_band or "low"
        band_feats = [1.0 if band == b else 0.0 for b in _BANDS]

        wt = row.week_type or "weekend"
        wt_feats = [1.0 if wt == w else 0.0 for w in _WEEK_TYPES]

        has_block = 1.0 if row.blocked_reason else 0.0

        picks = set(row.ticket_pick_simple or [])
        pick_feats = [1.0 if o in picks else 0.0 for o in ("1", "X", "2")]

        return [ph, pd, pa, *band_feats, *wt_feats, has_block, *pick_feats]

    @staticmethod
    def encode_prediction(prediction: MatchPredictionResponse, *, week_type: str) -> list[float]:
        vector = prediction.decision_probabilities or prediction.probabilities or {}
        ph = float(vector.get("L", prediction.home_probability))
        pd = float(vector.get("E", prediction.draw_probability))
        pa = float(vector.get("V", prediction.away_probability))
        band = prediction.confidence_band or "low"
        band_feats = [1.0 if band == b else 0.0 for b in _BANDS]
        wt_feats = [1.0 if week_type == w else 0.0 for w in _WEEK_TYPES]
        has_block = 1.0 if prediction.final_status == "BLOQUEADO" or prediction.flags else 0.0
        pick = getattr(prediction.recommended_outcome, "value", prediction.recommended_outcome)
        pick_feats = [1.0 if pick == o else 0.0 for o in ("1", "X", "2")]
        return [ph, pd, pa, *band_feats, *wt_feats, has_block, *pick_feats]


# ---------------------------------------------------------------------------
# Pure-numpy MLP
# ---------------------------------------------------------------------------

class _NumpyMLP:
    """Two-hidden-layer MLP with ReLU activations and softmax output.

    Forward:
        Z1 = X  @ W1 + b1  → A1 = ReLU(Z1)
        Z2 = A1 @ W2 + b2  → A2 = ReLU(Z2)
        Z3 = A2 @ W3 + b3  → out = softmax(Z3)

    Backward:
        Standard chain-rule; dL/dZ_out = softmax_out - one_hot(y).
        Each hidden layer gets ReLU gradient applied to its input.

    Weights are stored as lists so layer count is dynamic (matches
    ``hidden_dims``).  He-normal initialization for ReLU layers.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dims: list[int],
        output_dim: int,
        seed: int = 42,
    ) -> None:
        rng = np.random.default_rng(seed)
        dims = [input_dim] + hidden_dims + [output_dim]
        self.weights: list[np.ndarray] = []
        self.biases: list[np.ndarray] = []
        for i in range(len(dims) - 1):
            scale = np.sqrt(2.0 / dims[i])
            self.weights.append(rng.standard_normal((dims[i], dims[i + 1])).astype(np.float32) * scale)
            self.biases.append(np.zeros(dims[i + 1], dtype=np.float32))
        self._n_layers = len(self.weights)

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _relu(x: np.ndarray) -> np.ndarray:
        return np.maximum(0.0, x)

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        shifted = x - x.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / exp.sum(axis=1, keepdims=True)

    @staticmethod
    def _cross_entropy(probs: np.ndarray, y: np.ndarray) -> float:
        n = len(y)
        clipped = np.clip(probs[np.arange(n), y], 1e-9, 1.0)
        return float(-np.mean(np.log(clipped)))

    @staticmethod
    def _brier_score(probs: np.ndarray, y: np.ndarray) -> float:
        n, k = probs.shape
        one_hot = np.zeros((n, k), dtype=np.float32)
        one_hot[np.arange(n), y] = 1.0
        return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))

    # --- forward ---------------------------------------------------------

    def forward(self, X: np.ndarray) -> np.ndarray:
        """Run forward pass and cache activations for backprop."""
        self._cache: list[np.ndarray] = [X]
        A = X
        for i in range(self._n_layers - 1):
            A = self._relu(A @ self.weights[i] + self.biases[i])
            self._cache.append(A)
        out = self._softmax(A @ self.weights[-1] + self.biases[-1])
        self._cache.append(out)
        return out

    # --- backward --------------------------------------------------------

    def backward(self, y: np.ndarray, lr: float) -> None:
        """SGD update on one batch."""
        n = len(y)
        # Gradient at output: dL/dZ_out = softmax - one_hot
        dA = self._cache[-1].copy()
        dA[np.arange(n), y] -= 1.0
        dA /= n

        for i in range(self._n_layers - 1, -1, -1):
            A_prev = self._cache[i]
            dW = A_prev.T @ dA
            db = dA.sum(axis=0)
            self.weights[i] -= lr * dW
            self.biases[i] -= lr * db
            if i > 0:
                dA = dA @ self.weights[i].T
                dA *= (A_prev > 0).astype(np.float32)

    # --- training --------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        *,
        epochs: int = 150,
        lr: float = 0.01,
        batch_size: int = 32,
        seed: int = 42,
    ) -> list[float]:
        """Train in-place; return per-epoch loss history."""
        rng = np.random.default_rng(seed)
        n = len(X)
        history: list[float] = []
        for _ in range(epochs):
            idx = rng.permutation(n)
            epoch_loss = 0.0
            n_batches = 0
            for start in range(0, n, batch_size):
                bi = idx[start : start + batch_size]
                probs = self.forward(X[bi])
                epoch_loss += self._cross_entropy(probs, y[bi])
                self.backward(y[bi], lr)
                n_batches += 1
            history.append(epoch_loss / max(n_batches, 1))
        return history

    # --- inference -------------------------------------------------------

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.forward(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_proba(X).argmax(axis=1)

    # --- persistence (dict, not pickle) ----------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": [w.tolist() for w in self.weights],
            "biases": [b.tolist() for b in self.biases],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, input_dim: int, hidden_dims: list[int], output_dim: int) -> "_NumpyMLP":
        obj = cls.__new__(cls)
        obj.weights = [np.array(w, dtype=np.float32) for w in data["weights"]]
        obj.biases = [np.array(b, dtype=np.float32) for b in data["biases"]]
        obj._n_layers = len(obj.weights)
        obj._cache = []
        return obj


# ---------------------------------------------------------------------------
# Model wrapper
# ---------------------------------------------------------------------------

class NeuralBaselineModel:
    """Thin wrapper that adds evaluate/compare helpers around ``_NumpyMLP``."""

    def __init__(self, config: NeuralBaselineConfig | None = None) -> None:
        self.config = config or NeuralBaselineConfig()
        self._mlp: _NumpyMLP | None = None
        self._train_history: list[float] = []
        self._trained_on_rows: int = 0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "NeuralBaselineModel":
        cfg = self.config
        self._mlp = _NumpyMLP(
            input_dim=X.shape[1],
            hidden_dims=cfg.hidden_dims,
            output_dim=3,
            seed=cfg.random_seed,
        )
        self._train_history = self._mlp.fit(
            X, y,
            epochs=cfg.epochs,
            lr=cfg.learning_rate,
            batch_size=cfg.batch_size,
            seed=cfg.random_seed,
        )
        self._trained_on_rows = len(X)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._mlp is None:
            raise RuntimeError("Model has not been trained. Call fit() first.")
        return self._mlp.predict_proba(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_proba(X).argmax(axis=1)

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> dict[str, Any]:
        probs = self.predict_proba(X)
        preds = probs.argmax(axis=1)
        acc = float(np.mean(preds == y))
        brier = float(_NumpyMLP._brier_score(probs, y))
        ce = float(_NumpyMLP._cross_entropy(probs, y))
        n = len(y)
        per_class: dict[str, dict[str, float | None]] = {}
        for cls_idx, cls_label in IDX_TO_RESULT.items():
            mask = y == cls_idx
            total = int(mask.sum())
            hits = int(((preds == cls_idx) & mask).sum())
            per_class[cls_label] = {
                "total": total,
                "correct": hits,
                "recall": round(hits / total, 4) if total > 0 else None,
            }
        return {
            "n": n,
            "accuracy": round(acc, 4),
            "brier_score": round(brier, 4),
            "cross_entropy": round(ce, 4),
            "per_class": per_class,
            "final_train_loss": round(self._train_history[-1], 4) if self._train_history else None,
            "trained_on_rows": self._trained_on_rows,
        }

    def to_artifact(self) -> dict[str, Any]:
        if self._mlp is None:
            raise RuntimeError("No trained model to serialize.")
        cfg = self.config
        return {
            "model_type": cfg.model_type,
            "is_production": cfg.is_production,
            "architecture": {
                "input_dim": INPUT_DIM,
                "hidden_dims": cfg.hidden_dims,
                "output_dim": 3,
                "activation": "relu",
                "output_activation": "softmax",
            },
            "hyperparameters": {
                "learning_rate": cfg.learning_rate,
                "epochs": cfg.epochs,
                "batch_size": cfg.batch_size,
                "random_seed": cfg.random_seed,
            },
            "feature_names": FEATURE_NAMES,
            "shadow_safe": True,
            "post_result_features_used": False,
            "label_map": IDX_TO_RESULT,
            "trained_on_rows": self._trained_on_rows,
            "train_loss_history": self._train_history,
            "weights": self._mlp.to_dict(),
        }

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> "NeuralBaselineModel":
        cfg = NeuralBaselineConfig(
            hidden_dims=artifact["architecture"]["hidden_dims"],
            learning_rate=artifact["hyperparameters"]["learning_rate"],
            epochs=artifact["hyperparameters"]["epochs"],
            batch_size=artifact["hyperparameters"]["batch_size"],
            random_seed=artifact["hyperparameters"]["random_seed"],
        )
        obj = cls(cfg)
        obj._mlp = _NumpyMLP.from_dict(
            artifact["weights"],
            input_dim=artifact["architecture"]["input_dim"],
            hidden_dims=artifact["architecture"]["hidden_dims"],
            output_dim=artifact["architecture"]["output_dim"],
        )
        obj._trained_on_rows = artifact.get("trained_on_rows", 0)
        obj._train_history = artifact.get("train_loss_history", [])
        return obj


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class NeuralBaselineService:
    """Orchestrates offline training, evaluation, and comparison.

    Safety invariants (always enforced):
    - Never creates a ``ModelTrainingRunModel`` with production data.
    - Never mutates existing predictions or scoring records.
    - When ``trainable_rows < config.min_rows`` every method returns a
      status dict with ``status="not_enough_data"`` instead of raising.
    """

    def __init__(
        self,
        rows: list[AdaptiveDatasetRow],
        config: NeuralBaselineConfig | None = None,
    ) -> None:
        self.rows = rows
        self.config = config or NeuralBaselineConfig()

    # --- public interface -------------------------------------------------

    def readiness(self) -> dict[str, Any]:
        """Return dataset readiness without training."""
        n = len(self.rows)
        ready = n >= self.config.min_rows
        return {
            "status": "ready" if ready else "not_enough_data",
            "trainable_rows": n,
            "min_rows_required": self.config.min_rows,
            "rows_needed": max(0, self.config.min_rows - n),
            "model_type": self.config.model_type,
            "is_production": self.config.is_production,
            "feature_names": FEATURE_NAMES,
            "architecture": {
                "input_dim": INPUT_DIM,
                "hidden_dims": self.config.hidden_dims,
                "output_dim": 3,
            },
        }

    def dry_run_train(self) -> dict[str, Any]:
        """Check readiness and simulate training without saving anything."""
        n = len(self.rows)
        if n < self.config.min_rows:
            return {
                **self.readiness(),
                "trained": False,
                "reason": f"Need {self.config.min_rows} rows, have {n}.",
            }
        try:
            X, y, _ = NeuralDatasetBuilder().build(self.rows)
        except ValueError as exc:
            return {"status": "not_enough_data", "trained": False, "reason": str(exc)}

        model = NeuralBaselineModel(self.config).fit(X, y)
        metrics = model.evaluate(X, y)
        artifact = model.to_artifact()
        return {
            "status": "ok",
            "trained": True,
            "saved": False,
            "trainable_rows": n,
            "encoded_rows": len(X),
            "metrics": metrics,
            "comparison": self.compare_against_baseline(artifact),
        }

    def train_offline(self) -> dict[str, Any]:
        """Train and return a serializable experimental artifact.

        Does NOT write to any DB table. The artifact dict is returned to
        the caller, who can inspect or store it outside the production
        model registry.
        """
        n = len(self.rows)
        if n < self.config.min_rows:
            return {**self.readiness(), "trained": False}
        try:
            X, y, _ = NeuralDatasetBuilder().build(self.rows)
        except ValueError as exc:
            return {"status": "not_enough_data", "trained": False, "reason": str(exc)}

        model = NeuralBaselineModel(self.config).fit(X, y)
        artifact = model.to_artifact()
        metrics = model.evaluate(X, y)
        logger.info(
            "neural_baseline_trained_offline",
            extra={
                "event": "neural_baseline_trained_offline",
                "rows": len(X),
                "accuracy": metrics["accuracy"],
                "brier": metrics["brier_score"],
            },
        )
        return {
            "status": "ok",
            "trained": True,
            "saved": False,
            "is_production": False,
            "trainable_rows": n,
            "encoded_rows": len(X),
            "metrics": metrics,
            "artifact": artifact,
        }

    def evaluate_offline(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """Score a previously-trained experimental artifact on current rows."""
        if artifact.get("is_production", False):
            raise ValueError("evaluate_offline refuses production artifacts.")
        n = len(self.rows)
        if n == 0:
            return {"status": "not_enough_data", "trainable_rows": 0}
        try:
            X, y, _ = NeuralDatasetBuilder().build(self.rows)
        except ValueError as exc:
            return {"status": "not_enough_data", "reason": str(exc)}

        model = NeuralBaselineModel.from_artifact(artifact)
        metrics = model.evaluate(X, y)
        return {
            "status": "ok",
            "evaluated_rows": len(X),
            "metrics": metrics,
        }

    def compare_against_baseline(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """Compare neural model against the XGBoost/heuristic stored probabilities.

        "Baseline" = the prob_home/draw/away already stored in each
        AdaptiveDatasetRow (i.e. what the production model predicted at
        scoring time).

        "Neural" = this experimental model's predictions on the same rows.

        Returns side-by-side accuracy, brier, and cross-entropy.
        """
        if artifact.get("is_production", False):
            raise ValueError("compare_against_baseline refuses production artifacts.")
        n = len(self.rows)
        if n == 0:
            return {"status": "not_enough_data", "trainable_rows": 0}
        try:
            X, y, _ = NeuralDatasetBuilder().build(self.rows)
        except ValueError as exc:
            return {"status": "not_enough_data", "reason": str(exc)}

        # Baseline probabilities from stored predictions
        valid_rows = [
            r for r in self.rows
            if RESULT_TO_IDX.get(r.actual_result) is not None
            and not (r.prob_home is None and r.prob_draw is None and r.prob_away is None)
        ]
        baseline_probs = np.array(
            [[r.prob_home or 1/3, r.prob_draw or 1/3, r.prob_away or 1/3] for r in valid_rows],
            dtype=np.float32,
        )
        baseline_y = np.array(
            [RESULT_TO_IDX[r.actual_result] for r in valid_rows],
            dtype=np.int64,
        )

        # Neural model predictions
        model = NeuralBaselineModel.from_artifact(artifact)
        neural_probs = model.predict_proba(X)

        baseline_metrics = {
            "accuracy": round(float(np.mean(baseline_probs.argmax(axis=1) == baseline_y)), 4),
            "brier_score": round(float(_NumpyMLP._brier_score(baseline_probs, baseline_y)), 4),
            "cross_entropy": round(float(_NumpyMLP._cross_entropy(baseline_probs, baseline_y)), 4),
        }
        neural_metrics = {
            "accuracy": round(float(np.mean(neural_probs.argmax(axis=1) == y)), 4),
            "brier_score": round(float(_NumpyMLP._brier_score(neural_probs, y)), 4),
            "cross_entropy": round(float(_NumpyMLP._cross_entropy(neural_probs, y)), 4),
        }

        brier_delta = round(baseline_metrics["brier_score"] - neural_metrics["brier_score"], 4)
        acc_delta = round(neural_metrics["accuracy"] - baseline_metrics["accuracy"], 4)

        return {
            "status": "ok",
            "evaluated_rows": len(X),
            "baseline": baseline_metrics,
            "neural": neural_metrics,
            "brier_delta": brier_delta,
            "accuracy_delta": acc_delta,
            "neural_better_brier": brier_delta > 0,
            "neural_better_accuracy": acc_delta > 0,
        }


class NeuralBaselineRegistryService:
    """Persist and promote neural baseline artifacts safely.

    This registry intentionally uses neural-specific ``model_name`` values so
    it cannot replace the production ``elo_poisson_blend`` artifact by
    accident. The latest ``neural_baseline_active`` row is the active neural
    candidate; rollback appends a copy of the previous active row.
    """

    def __init__(
        self,
        rows: list[AdaptiveDatasetRow],
        training_repository: TrainingRepository,
        config: NeuralBaselineConfig | None = None,
    ) -> None:
        self.rows = rows
        self.training_repository = training_repository
        self.config = config or NeuralBaselineConfig()

    def train_candidate(self) -> dict[str, Any]:
        svc = NeuralBaselineService(self.rows, self.config)
        result = svc.train_offline()
        if not result.get("trained"):
            return {**result, "saved": False}

        artifact = result["artifact"]
        comparison = svc.compare_against_baseline(artifact)
        artifact.update(
            {
                "model_name": NEURAL_CANDIDATE_MODEL_NAME,
                "lifecycle_status": "candidate",
                "is_production": False,
                "saved_at": _utc_iso(),
                "metrics": result["metrics"],
                "comparison": comparison,
                "dataset": self._dataset_summary(),
            }
        )
        run = self.training_repository.save_run(
            NEURAL_CANDIDATE_MODEL_NAME,
            int(result["encoded_rows"]),
            artifact,
        )
        return {
            "status": "ok",
            "trained": True,
            "saved": True,
            "candidate_run_id": run.id,
            "model_name": run.model_name,
            "trained_at": run.trained_at,
            "trainable_rows": result["trainable_rows"],
            "encoded_rows": result["encoded_rows"],
            "metrics": result["metrics"],
            "comparison": comparison,
        }

    def latest_candidate(self, *, include_artifact: bool = False) -> dict[str, Any]:
        run = self.training_repository.latest_run(NEURAL_CANDIDATE_MODEL_NAME)
        return self._run_payload(
            run,
            missing_status="no_candidate",
            include_artifact=include_artifact,
        )

    def active(self, *, include_artifact: bool = False) -> dict[str, Any]:
        run = self.training_repository.latest_run(NEURAL_ACTIVE_MODEL_NAME)
        return self._run_payload(
            run,
            missing_status="no_active_model",
            include_artifact=include_artifact,
        )

    def promote_candidate(
        self,
        *,
        candidate_run_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        candidate = (
            self.training_repository.get_run(candidate_run_id)
            if candidate_run_id
            else self.training_repository.latest_run(NEURAL_CANDIDATE_MODEL_NAME)
        )
        if candidate is None or candidate.model_name != NEURAL_CANDIDATE_MODEL_NAME:
            return {"status": "not_found", "promoted": False, "reason": "candidate_not_found"}

        artifact = _artifact(candidate)
        comparison = artifact.get("comparison") or {}
        if not force and not (
            comparison.get("neural_better_brier") is True
            and float(comparison.get("brier_delta") or 0.0) > 0.0
        ):
            return {
                "status": "blocked",
                "promoted": False,
                "reason": "candidate_does_not_improve_brier",
                "candidate_run_id": candidate.id,
                "comparison": comparison,
            }

        previous_active = self.training_repository.latest_run(NEURAL_ACTIVE_MODEL_NAME)
        active_artifact = {
            **artifact,
            "model_name": NEURAL_ACTIVE_MODEL_NAME,
            "lifecycle_status": "active",
            "is_production": False,
            "source_candidate_run_id": candidate.id,
            "previous_active_run_id": previous_active.id if previous_active else None,
            "promoted_at": _utc_iso(),
        }
        run = self.training_repository.save_run(
            NEURAL_ACTIVE_MODEL_NAME,
            candidate.training_sample_size,
            active_artifact,
        )
        return {
            "status": "ok",
            "promoted": True,
            "active_run_id": run.id,
            "candidate_run_id": candidate.id,
            "previous_active_run_id": previous_active.id if previous_active else None,
            "comparison": comparison,
            "rollback_available": previous_active is not None,
        }

    def rollback_active(self) -> dict[str, Any]:
        active_runs = self.training_repository.list_runs(NEURAL_ACTIVE_MODEL_NAME, limit=2)
        if len(active_runs) < 2:
            return {
                "status": "blocked",
                "rolled_back": False,
                "reason": "no_previous_active_run",
            }
        current, previous = active_runs[0], active_runs[1]
        previous_artifact = _artifact(previous)
        rollback_artifact = {
            **previous_artifact,
            "model_name": NEURAL_ACTIVE_MODEL_NAME,
            "lifecycle_status": "active",
            "is_production": False,
            "rollback_from_run_id": current.id,
            "rollback_source_run_id": previous.id,
            "rolled_back_at": _utc_iso(),
        }
        run = self.training_repository.save_run(
            NEURAL_ACTIVE_MODEL_NAME,
            previous.training_sample_size,
            rollback_artifact,
        )
        return {
            "status": "ok",
            "rolled_back": True,
            "active_run_id": run.id,
            "rollback_from_run_id": current.id,
            "rollback_source_run_id": previous.id,
        }

    def _dataset_summary(self) -> dict[str, Any]:
        sign_only = sum(1 for row in self.rows if not row.result_is_canonical)
        canonical = len(self.rows) - sign_only
        return {
            "rows": len(self.rows),
            "canonical_rows": canonical,
            "sign_only_rows": sign_only,
            "slates": len({row.slate_id for row in self.rows}),
        }

    @staticmethod
    def _run_payload(
        run: Any | None,
        *,
        missing_status: str,
        include_artifact: bool = False,
    ) -> dict[str, Any]:
        if run is None:
            return {"status": missing_status, "available": False}
        artifact = _artifact(run)
        payload = {
            "status": "ok",
            "available": True,
            "run_id": run.id,
            "model_name": run.model_name,
            "trained_at": run.trained_at,
            "training_sample_size": run.training_sample_size,
            "metrics": artifact.get("metrics"),
            "comparison": artifact.get("comparison"),
            "dataset": artifact.get("dataset"),
            "lifecycle_status": artifact.get("lifecycle_status"),
            "source_candidate_run_id": artifact.get("source_candidate_run_id"),
            "previous_active_run_id": artifact.get("previous_active_run_id"),
        }
        if include_artifact:
            payload["artifact"] = artifact
        return payload


def _artifact(run: Any) -> dict[str, Any]:
    import json

    return json.loads(run.artifact_json or "{}")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NeuralShadowService:
    """Apply active neural model as read-only shadow on prediction payloads."""

    def __init__(self, training_repository: TrainingRepository) -> None:
        self.training_repository = training_repository

    def apply_to_predictions(
        self,
        predictions: list[MatchPredictionResponse],
        *,
        week_type: str,
    ) -> None:
        run = self.training_repository.latest_run(NEURAL_ACTIVE_MODEL_NAME)
        if run is None:
            for pred in predictions:
                pred.neural_shadow = NeuralShadowInfo(active=False, status="no_active_model")
            return

        artifact = _artifact(run)
        if artifact.get("shadow_safe") is not True or artifact.get("feature_names") != FEATURE_NAMES:
            for pred in predictions:
                pred.neural_shadow = NeuralShadowInfo(
                    active=False,
                    status="incompatible_artifact",
                    run_id=run.id,
                    reason="active neural artifact is not pre-match shadow safe",
                )
            return

        try:
            model = NeuralBaselineModel.from_artifact(artifact)
            X = np.array(
                [NeuralDatasetBuilder.encode_prediction(pred, week_type=week_type) for pred in predictions],
                dtype=np.float32,
            )
            probs = model.predict_proba(X)
        except Exception as exc:  # pragma: no cover - diagnostic must not block predictions
            logger.exception("neural_shadow_failed", extra={"event": "neural_shadow_failed"})
            for pred in predictions:
                pred.neural_shadow = NeuralShadowInfo(
                    active=False,
                    status="error",
                    run_id=run.id,
                    reason=str(exc),
                )
            return

        for pred, row in zip(predictions, probs, strict=True):
            neural_probs = {
                "L": round(float(row[0]), 4),
                "E": round(float(row[1]), 4),
                "V": round(float(row[2]), 4),
            }
            baseline = pred.decision_probabilities or pred.probabilities
            delta = {
                k: round(neural_probs[k] - float(baseline.get(k, 0.0)), 4)
                for k in ("L", "E", "V")
            }
            top_pick = max(neural_probs, key=lambda key: neural_probs[key])
            baseline_top = max(baseline, key=lambda key: baseline[key])
            pred.neural_shadow = NeuralShadowInfo(
                active=True,
                status="ok",
                run_id=run.id,
                probabilities=neural_probs,
                top_pick=top_pick,
                baseline_top_pick=baseline_top,
                top_pick_changed=top_pick != baseline_top,
                probability_delta=delta,
                max_abs_delta=round(max(abs(v) for v in delta.values()), 4),
            )
