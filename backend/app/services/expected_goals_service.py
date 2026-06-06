"""XGBoost-based Expected Goals service (Sprint 7.1).

Predicts the Poisson lambda ``E[goals]`` for one side of a match given:
- rolling form (goals_for / goals_against over 5- and 10-game windows)
- points pace (points_per_match over the last 10)
- competition baseline (long-run mean home/away goals)
- side indicator (home vs away)
- days of rest

The trained Booster sits behind a thin orchestrator: ``train`` walks the
historical match table, builds two leakage-free training rows per match
(home perspective + away perspective), and fits a ``reg:squarederror``
XGBoost regressor. ``predict_lambda`` is a single-row inference helper
the Dixon-Coles outcome calculator can swap in for the current static
prior (1.45 home / 1.15 away).

The service is intentionally decoupled from ``ModelTrainingService``.
We bake the booster once, persist via ``artifact_storage``, and let the
existing pipeline read its predictions through a small adapter. That
way a broken xG model degrades to "use the heuristic baseline" instead
of contaminating the multiclass classifier.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services import artifact_storage
from app.services.expected_goals_features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    CompetitionBaseline,
    TeamRecentHistory,
    append_match_to_history,
    build_feature_row,
    empty_history,
    row_to_vector,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "expected_goals_v1"
# Threshold below which we refuse to train — too few rows produce a
# booster that just memorizes the baseline and adds noise. The legacy
# multi-class classifier uses 30; we keep the same floor here so cold
# competitions skip both models consistently.
MIN_TRAINING_SAMPLES = 60
# Booster hyperparameters. Tuned with a small grid on a 10K-match
# rehearsal: trees stay shallow because the feature count is small and
# we want the residual error to look Poissonian (overfitting kills the
# Dixon-Coles draw probabilities).
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "reg:squarederror",
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "min_child_weight": 2.0,
    "reg_lambda": 1.0,
    "seed": 42,
    "eval_metric": "rmse",
    "verbosity": 0,
}
DEFAULT_NUM_BOOST_ROUND = 240
# Predicted lambdas are clamped to the same band the heuristic priors
# enforce — a flat 0 or a runaway 6.0 prediction blows up the
# Dixon-Coles outcome grid for no real-world gain.
LAMBDA_FLOOR_HOME = 0.4
LAMBDA_FLOOR_AWAY = 0.3
LAMBDA_CEILING = 5.0


@dataclass(frozen=True)
class TrainingRow:
    """One side-perspective row fed to the regressor."""

    features: list[float]
    target_goals: float
    kickoff: datetime


@dataclass(frozen=True)
class TrainingDataset:
    rows: list[list[float]]
    targets: list[float]
    kickoffs: list[datetime]

    @property
    def sample_size(self) -> int:
        return len(self.targets)


def _competition_key(match: Any) -> str:
    """Best-effort competition slug. Falls back to ``"_default"`` when a
    match is missing competition metadata; the trainer treats the bucket
    uniformly because the per-competition baseline is also missing."""
    competition = getattr(match, "competition", None)
    name = getattr(competition, "name", None) or "_default"
    return name


def _league_baselines(matches_with_results: list[tuple[Any, Any]]) -> dict[str, CompetitionBaseline]:
    """Long-run home/away goal means per competition.

    Computed once over the whole training corpus so per-match feature
    construction is O(1). Brand-new competitions fall back to the
    package defaults (1.45 / 1.15)."""
    sums: dict[str, dict[str, float]] = defaultdict(
        lambda: {"home_goals": 0.0, "away_goals": 0.0, "count": 0.0}
    )
    for match, result in matches_with_results:
        bucket = sums[_competition_key(match)]
        bucket["home_goals"] += float(result.home_goals)
        bucket["away_goals"] += float(result.away_goals)
        bucket["count"] += 1.0
    out: dict[str, CompetitionBaseline] = {}
    for key, bucket in sums.items():
        if bucket["count"] <= 0:
            continue
        out[key] = CompetitionBaseline(
            home_goals=bucket["home_goals"] / bucket["count"],
            away_goals=bucket["away_goals"] / bucket["count"],
        )
    return out


def build_training_dataset(
    matches_with_results: list[tuple[Any, Any]],
) -> TrainingDataset:
    """Produce a leakage-free training dataset.

    ``matches_with_results`` is iterated in chronological order; each
    side's rolling history is updated **only after** the row for the
    current match is emitted, so the booster never trains on its own
    target."""
    ordered = sorted(
        matches_with_results,
        key=lambda pair: (
            pair[1].played_at
            if pair[1].played_at.tzinfo is not None
            else pair[1].played_at.replace(tzinfo=timezone.utc)
        ),
    )
    baselines = _league_baselines(ordered)
    team_history: dict[str, TeamRecentHistory] = defaultdict(empty_history)
    rows: list[list[float]] = []
    targets: list[float] = []
    kickoffs: list[datetime] = []
    for match, result in ordered:
        home_team_id = getattr(match.home_team, "id", None) or "unknown_home"
        away_team_id = getattr(match.away_team, "id", None) or "unknown_away"
        kickoff = result.played_at
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        baseline = baselines.get(_competition_key(match), CompetitionBaseline())
        home_row = build_feature_row(
            history=team_history[home_team_id],
            is_home=True,
            kickoff=kickoff,
            competition_baseline=baseline,
        )
        away_row = build_feature_row(
            history=team_history[away_team_id],
            is_home=False,
            kickoff=kickoff,
            competition_baseline=baseline,
        )
        rows.append(row_to_vector(home_row))
        targets.append(float(result.home_goals))
        kickoffs.append(kickoff)
        rows.append(row_to_vector(away_row))
        targets.append(float(result.away_goals))
        kickoffs.append(kickoff)
        # Update history AFTER emitting both rows so the away row above
        # cannot see the home row's goals.
        team_history[home_team_id] = append_match_to_history(
            team_history[home_team_id],
            goals_for=int(result.home_goals),
            goals_against=int(result.away_goals),
            kickoff=kickoff,
        )
        team_history[away_team_id] = append_match_to_history(
            team_history[away_team_id],
            goals_for=int(result.away_goals),
            goals_against=int(result.home_goals),
            kickoff=kickoff,
        )
    return TrainingDataset(rows=rows, targets=targets, kickoffs=kickoffs)


def train(
    matches_with_results: list[tuple[Any, Any]],
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    persist_root: Path | None = None,
) -> dict[str, Any] | None:
    """Fit the xG booster and persist it via ``artifact_storage``.

    Returns the artifact descriptor (including the storage path) or
    ``None`` when there is not enough data to train responsibly. The
    caller is responsible for storing the descriptor in
    ``training_runs`` (or wherever it tracks model lineage)."""
    dataset = build_training_dataset(matches_with_results)
    if dataset.sample_size < MIN_TRAINING_SAMPLES:
        logger.info(
            "expected_goals.train skipped: %d rows < %d minimum",
            dataset.sample_size,
            MIN_TRAINING_SAMPLES,
        )
        return None
    import xgboost as xgb

    booster_params = dict(DEFAULT_PARAMS)
    if params:
        booster_params.update(params)
    dtrain = xgb.DMatrix(dataset.rows, label=dataset.targets)
    booster = xgb.train(booster_params, dtrain, num_boost_round=num_boost_round)
    booster_json = booster.save_raw(raw_format="json").decode("utf-8")
    run_id = uuid4().hex
    descriptor = artifact_storage.save_booster_json(MODEL_NAME, run_id, booster_json)
    return {
        "model_type": "xgboost_regression",
        "model_name": MODEL_NAME,
        "run_id": run_id,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_names": list(FEATURE_NAMES),
        "feature_version": FEATURE_VERSION,
        "training_sample_size": dataset.sample_size,
        "xgboost_params": booster_params,
        "xgboost_num_boost_round": num_boost_round,
        "booster_descriptor": descriptor,
    }


def _clamp_lambda(value: float, *, floor: float) -> float:
    return max(floor, min(value, LAMBDA_CEILING))


def predict_lambda(
    *,
    booster_json: str,
    history: TeamRecentHistory,
    is_home: bool,
    kickoff: datetime,
    competition_baseline: CompetitionBaseline,
) -> float:
    """Score a single side. Returns the clamped Poisson lambda."""
    import xgboost as xgb

    row = build_feature_row(
        history=history,
        is_home=is_home,
        kickoff=kickoff,
        competition_baseline=competition_baseline,
    )
    booster = xgb.Booster()
    booster.load_model(bytearray(booster_json, "utf-8"))
    dmat = xgb.DMatrix([row_to_vector(row)])
    [raw] = booster.predict(dmat).tolist()
    floor = LAMBDA_FLOOR_HOME if is_home else LAMBDA_FLOOR_AWAY
    return _clamp_lambda(float(raw), floor=floor)


def load_booster_from_descriptor(descriptor: dict[str, str]) -> str | None:
    """Round-trip helper so the prediction service can hand a stored
    artifact descriptor to ``predict_lambda`` without knowing the
    storage layout."""
    return artifact_storage.load_booster_json(descriptor)


def evaluate_rmse(
    matches_with_results: list[tuple[Any, Any]],
    *,
    booster_json: str,
) -> dict[str, float]:
    """Walk the historical sample one more time and report goal-level
    RMSE / MAE against the trained booster. Lightweight regression
    metric — does not split train/test; the caller is expected to pass
    held-out matches."""
    import xgboost as xgb

    dataset = build_training_dataset(matches_with_results)
    if dataset.sample_size == 0:
        return {"rmse": 0.0, "mae": 0.0, "samples": 0.0}
    booster = xgb.Booster()
    booster.load_model(bytearray(booster_json, "utf-8"))
    dmat = xgb.DMatrix(dataset.rows)
    preds = booster.predict(dmat).tolist()
    n = len(preds)
    squared_error = 0.0
    absolute_error = 0.0
    for pred, target in zip(preds, dataset.targets, strict=True):
        diff = float(pred) - target
        squared_error += diff * diff
        absolute_error += abs(diff)
    return {
        "rmse": (squared_error / n) ** 0.5,
        "mae": absolute_error / n,
        "samples": float(n),
    }


def baseline_rmse(matches_with_results: list[tuple[Any, Any]]) -> dict[str, float]:
    """RMSE/MAE of the naive baseline that always predicts the
    competition mean. Used as the gate the xG model has to beat."""
    if not matches_with_results:
        return {"rmse": 0.0, "mae": 0.0, "samples": 0.0}
    baselines = _league_baselines(matches_with_results)
    squared_error = 0.0
    absolute_error = 0.0
    n = 0
    for match, result in matches_with_results:
        baseline = baselines.get(_competition_key(match), CompetitionBaseline())
        for predicted, actual in (
            (baseline.home_goals, float(result.home_goals)),
            (baseline.away_goals, float(result.away_goals)),
        ):
            diff = predicted - actual
            squared_error += diff * diff
            absolute_error += abs(diff)
            n += 1
    if n == 0:
        return {"rmse": 0.0, "mae": 0.0, "samples": 0.0}
    return {
        "rmse": (squared_error / n) ** 0.5,
        "mae": absolute_error / n,
        "samples": float(n),
    }


def artifact_summary(artifact: dict[str, Any]) -> str:
    """Human-readable summary for logging / CLI output."""
    return json.dumps(
        {
            "model_name": artifact.get("model_name"),
            "trained_at": artifact.get("trained_at"),
            "samples": artifact.get("training_sample_size"),
            "feature_version": artifact.get("feature_version"),
            "rounds": artifact.get("xgboost_num_boost_round"),
        },
        indent=2,
    )
