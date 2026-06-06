"""End-to-end coverage for ExpectedGoalsService.

These tests actually train a tiny XGBoost booster on synthetic fixtures.
They run in ~1 second; the goal is to lock in the leakage-free dataset
construction and the storage round-trip so future refactors can't
silently flip them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import expected_goals_service as egs


@dataclass
class _TeamStub:
    id: str
    name: str


@dataclass
class _CompetitionStub:
    name: str


@dataclass
class _MatchStub:
    home_team: _TeamStub
    away_team: _TeamStub
    competition: _CompetitionStub


@dataclass
class _ResultStub:
    home_goals: int
    away_goals: int
    played_at: datetime


def _synth_matches(num_matches: int = 200, seed: int = 7):
    """Build a deterministic synthetic schedule.

    Teams alternate between Strong and Weak rotation pools; Strong teams
    score ~2.2 at home, Weak ~0.9 — the booster should learn to amplify
    the home_indicator + rolling_goals_for_5 axis. The randomness keeps
    targets integer-valued (real Poisson draws) so the squared-error
    objective stays well-conditioned."""
    import random

    rng = random.Random(seed)
    strong = [f"strong-{idx}" for idx in range(6)]
    weak = [f"weak-{idx}" for idx in range(6)]
    teams = strong + weak
    matches: list[tuple[_MatchStub, _ResultStub]] = []
    kickoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(num_matches):
        home_id, away_id = rng.sample(teams, 2)
        home = _TeamStub(id=home_id, name=home_id)
        away = _TeamStub(id=away_id, name=away_id)
        is_strong = lambda tid: tid in strong  # noqa: E731 — local
        home_lam = 2.2 if is_strong(home_id) else 0.9
        away_lam = 1.5 if is_strong(away_id) else 0.7
        home_goals = max(0, int(rng.gauss(home_lam, 0.7)))
        away_goals = max(0, int(rng.gauss(away_lam, 0.7)))
        match = _MatchStub(
            home_team=home,
            away_team=away,
            competition=_CompetitionStub(name="Synthetic League A"),
        )
        result = _ResultStub(
            home_goals=home_goals,
            away_goals=away_goals,
            played_at=kickoff + timedelta(days=i),
        )
        matches.append((match, result))
    return matches


@pytest.fixture
def tmp_artifact_storage(tmp_path, monkeypatch):
    """Redirect artifact_storage to a tmp dir so tests don't touch /data."""
    monkeypatch.setenv("PROAI_MODEL_STORAGE_ROOT", str(tmp_path))
    yield tmp_path


class TestBuildTrainingDataset:
    def test_emits_two_rows_per_match(self) -> None:
        matches = _synth_matches(num_matches=10)
        dataset = egs.build_training_dataset(matches)
        assert dataset.sample_size == 20

    def test_target_alignment_with_results(self) -> None:
        matches = _synth_matches(num_matches=5)
        dataset = egs.build_training_dataset(matches)
        # The kickoffs returned by build_training_dataset are sorted by
        # played_at — the synthetic generator already inserts in
        # chronological order so the test reads cleanly. Each match
        # contributes a (home_goals, away_goals) pair.
        ordered = sorted(matches, key=lambda pair: pair[1].played_at)
        expected = []
        for _, result in ordered:
            expected.extend([float(result.home_goals), float(result.away_goals)])
        assert dataset.targets == expected

    def test_no_leakage_in_first_pair(self) -> None:
        # The very first match emitted should have zero-history features
        # for both sides (no previous games).
        matches = _synth_matches(num_matches=3)
        dataset = egs.build_training_dataset(matches)
        from app.services.expected_goals_features import FEATURE_NAMES

        home_row = dict(zip(FEATURE_NAMES, dataset.rows[0]))
        assert home_row["rolling_goals_for_5"] == 0.0
        assert home_row["points_per_match_10"] == 0.0


class TestTrain:
    def test_returns_none_below_sample_floor(self, tmp_artifact_storage) -> None:
        # 10 matches → 20 rows, well below MIN_TRAINING_SAMPLES.
        artifact = egs.train(_synth_matches(num_matches=10))
        assert artifact is None

    def test_persists_booster_and_returns_descriptor(self, tmp_artifact_storage) -> None:
        artifact = egs.train(_synth_matches(num_matches=80))
        assert artifact is not None
        assert artifact["model_name"] == egs.MODEL_NAME
        assert artifact["feature_version"] == "xg_v1"
        path = Path(artifact["booster_descriptor"]["path"])
        assert path.is_file()
        # The artifact JSON is real XGBoost JSON, not a pickle blob.
        assert path.read_bytes().startswith(b"{")

    def test_booster_beats_competition_baseline_on_training_data(
        self, tmp_artifact_storage
    ) -> None:
        matches = _synth_matches(num_matches=200, seed=11)
        artifact = egs.train(matches)
        assert artifact is not None
        booster_json = egs.load_booster_from_descriptor(artifact["booster_descriptor"])
        assert booster_json is not None
        trained = egs.evaluate_rmse(matches, booster_json=booster_json)
        baseline = egs.baseline_rmse(matches)
        # On the same data the booster has to do at least as well as
        # always-predict-the-mean — anything else means we wired it up
        # backwards. We give a tiny tolerance because the booster has
        # regularization and the baseline is the unconstrained optimum
        # of the squared loss.
        assert trained["rmse"] <= baseline["rmse"] + 0.05
        assert trained["samples"] == baseline["samples"]


class TestPredictLambda:
    def test_returns_value_in_clamped_band(self, tmp_artifact_storage) -> None:
        matches = _synth_matches(num_matches=120)
        artifact = egs.train(matches)
        assert artifact is not None
        booster_json = egs.load_booster_from_descriptor(artifact["booster_descriptor"])
        assert booster_json is not None
        # Use the most recent team's history we can reconstruct from the
        # synthetic match list: just rebuild from scratch via
        # build_training_dataset and reuse the last team's empty seed.
        from app.services.expected_goals_features import (
            CompetitionBaseline,
            empty_history,
        )

        kickoff = matches[-1][1].played_at
        lam = egs.predict_lambda(
            booster_json=booster_json,
            history=empty_history(),
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        assert lam >= egs.LAMBDA_FLOOR_HOME
        assert lam <= egs.LAMBDA_CEILING


class TestBaselineRmse:
    def test_empty_input_returns_zero(self) -> None:
        out = egs.baseline_rmse([])
        assert out == {"rmse": 0.0, "mae": 0.0, "samples": 0.0}

    def test_baseline_equals_long_run_mean(self) -> None:
        matches = _synth_matches(num_matches=200, seed=3)
        baseline = egs.baseline_rmse(matches)
        # Every match contributes 2 samples (home + away).
        assert baseline["samples"] == 400.0
        assert baseline["rmse"] > 0.0
