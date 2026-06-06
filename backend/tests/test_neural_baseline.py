"""Tests for NeuralBaselineService — experimental offline neural baseline.

Coverage:
  1. dataset builder raises ValueError when no valid rows
  2. readiness returns not_enough_data when trainable_rows=0
  3. confidence_band one-hot encoding is correct for each band value
  4. target label encoding: "1"→0, "X"→1, "2"→2
  5. builder ignores rows without a valid actual_result
  6. builder ignores rows where all probabilities are None (no prediction made)
  7. offline training with synthetic data succeeds and returns metrics
  8. evaluate_offline returns accuracy/brier/cross_entropy metrics
  9. compare_against_baseline returns comparison dict with both sides
  10. train_offline does NOT create any production ModelTrainingRunModel row
  11. train_offline does NOT modify existing PredictionModel rows
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas.adaptive_dataset import AdaptiveDatasetRow
from app.services.neural_baseline_service import (
    FEATURE_NAMES,
    INPUT_DIM,
    RESULT_TO_IDX,
    NeuralBaselineConfig,
    NeuralBaselineService,
    NeuralDatasetBuilder,
    _NumpyMLP,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOW_CONFIG = NeuralBaselineConfig(min_rows=5, epochs=10, hidden_dims=[8, 4])


def _make_row(
    actual_result: str = "1",
    band: str = "high",
    week_type: str = "weekend",
    prob_home: float | None = 0.6,
    prob_draw: float | None = 0.25,
    prob_away: float | None = 0.15,
    ticket_pick: list[str] | None = None,
    ticket_hit: bool | None = True,
    brier: float | None = 0.3,
    blocked_reason: str | None = None,
) -> AdaptiveDatasetRow:
    return AdaptiveDatasetRow(
        slate_id="slate-test",
        draw_code="PG-NN-1",
        week_type=week_type,
        composition_hash="abc123",
        slate_version=1,
        match_id=str(uuid4()),
        position=1,
        home_team="Home",
        away_team="Away",
        competition="Liga Test",
        prob_home=prob_home,
        prob_draw=prob_draw,
        prob_away=prob_away,
        recommended_outcome=actual_result,
        confidence_band=band,
        blocked_reason=blocked_reason,
        actual_result=actual_result,
        home_goals=1,
        away_goals=0,
        hit=True,
        brier_score=brier,
        result_is_canonical=True,
        ticket_pick_simple=ticket_pick or ["1"],
        ticket_pick_doubles=None,
        ticket_pick_full=None,
        ticket_hit_simple=ticket_hit,
        ticket_hit_doubles=None,
        ticket_hit_full=None,
    )


def _synthetic_rows(n: int = 15) -> list[AdaptiveDatasetRow]:
    results = ["1", "X", "2"]
    bands = ["high", "medium", "low", "blocked"]
    week_types = ["weekend", "midweek"]
    rows = []
    for i in range(n):
        r = results[i % 3]
        rows.append(
            _make_row(
                actual_result=r,
                band=bands[i % 4],
                week_type=week_types[i % 2],
                prob_home=0.5 + 0.1 * (i % 3),
                prob_draw=0.25,
                prob_away=max(0.01, 0.25 - 0.1 * (i % 3)),
                ticket_pick=[r],
                ticket_hit=True,
                brier=0.2 + 0.05 * (i % 5),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# 1. Dataset builder raises when no valid rows
# ---------------------------------------------------------------------------

class TestNeuralDatasetBuilder:
    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            NeuralDatasetBuilder().build([])

    def test_invalid_result_rows_all_filtered(self):
        rows = [
            _make_row(actual_result="?"),
            _make_row(actual_result=""),
        ]
        with pytest.raises(ValueError):
            NeuralDatasetBuilder().build(rows)

    def test_all_none_probs_rows_filtered(self):
        row = _make_row(prob_home=None, prob_draw=None, prob_away=None)
        with pytest.raises(ValueError):
            NeuralDatasetBuilder().build([row])


# ---------------------------------------------------------------------------
# 2. Readiness gate
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_not_enough_data_when_zero_rows(self):
        svc = NeuralBaselineService(rows=[], config=_LOW_CONFIG)
        report = svc.readiness()
        assert report["status"] == "not_enough_data"
        assert report["trainable_rows"] == 0
        assert report["is_production"] is False

    def test_ready_when_enough_rows(self):
        rows = _synthetic_rows(10)
        svc = NeuralBaselineService(rows=rows, config=_LOW_CONFIG)
        report = svc.readiness()
        assert report["status"] == "ready"
        assert report["trainable_rows"] == 10

    def test_not_production(self):
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        report = svc.readiness()
        assert report["is_production"] is False
        assert report["model_type"] == "neural_baseline_experimental"


# ---------------------------------------------------------------------------
# 3. Confidence band encoding
# ---------------------------------------------------------------------------

class TestConfidenceBandEncoding:
    def _encode_single(self, band: str) -> list[float]:
        row = _make_row(band=band)
        X, _, _ = NeuralDatasetBuilder().build([row])
        return X[0].tolist()

    def test_high_band_one_hot(self):
        feats = self._encode_single("high")
        assert feats[4] == 1.0  # band_high
        assert feats[5] == 0.0
        assert feats[6] == 0.0
        assert feats[7] == 0.0

    def test_medium_band_one_hot(self):
        feats = self._encode_single("medium")
        assert feats[4] == 0.0
        assert feats[5] == 1.0  # band_medium
        assert feats[6] == 0.0
        assert feats[7] == 0.0

    def test_low_band_one_hot(self):
        feats = self._encode_single("low")
        assert feats[4] == 0.0
        assert feats[5] == 0.0
        assert feats[6] == 1.0  # band_low
        assert feats[7] == 0.0

    def test_blocked_band_one_hot(self):
        feats = self._encode_single("blocked")
        assert feats[4] == 0.0
        assert feats[5] == 0.0
        assert feats[6] == 0.0
        assert feats[7] == 1.0  # band_blocked

    def test_feature_vector_length(self):
        feats = self._encode_single("high")
        assert len(feats) == INPUT_DIM
        assert INPUT_DIM == len(FEATURE_NAMES)


# ---------------------------------------------------------------------------
# 4. Target encoding
# ---------------------------------------------------------------------------

class TestTargetEncoding:
    def test_home_win_maps_to_0(self):
        _, y, _ = NeuralDatasetBuilder().build([_make_row("1")])
        assert y[0] == 0

    def test_draw_maps_to_1(self):
        _, y, _ = NeuralDatasetBuilder().build([_make_row("X")])
        assert y[0] == 1

    def test_away_win_maps_to_2(self):
        _, y, _ = NeuralDatasetBuilder().build([_make_row("2")])
        assert y[0] == 2

    def test_result_to_idx_dict(self):
        assert RESULT_TO_IDX == {"1": 0, "X": 1, "2": 2}


# ---------------------------------------------------------------------------
# 5. Builder filters invalid actual_result
# ---------------------------------------------------------------------------

class TestBuilderFiltering:
    def test_ignores_unknown_actual_result(self):
        valid = _make_row("1")
        invalid = _make_row("?")
        X, y, _ = NeuralDatasetBuilder().build([valid, invalid])
        assert len(X) == 1
        assert y[0] == 0

    def test_ignores_none_probs(self):
        valid = _make_row("X")
        no_probs = _make_row("X", prob_home=None, prob_draw=None, prob_away=None)
        X, y, _ = NeuralDatasetBuilder().build([valid, no_probs])
        assert len(X) == 1

    def test_default_probs_when_partial_none(self):
        row = _make_row("1", prob_home=None, prob_draw=None, prob_away=0.4)
        X, _, _ = NeuralDatasetBuilder().build([row])
        # prob_home and prob_draw default to 1/3
        assert abs(X[0][0] - 1/3) < 1e-5
        assert abs(X[0][1] - 1/3) < 1e-5
        assert abs(X[0][2] - 0.4) < 1e-5


# ---------------------------------------------------------------------------
# 6. Offline training
# ---------------------------------------------------------------------------

class TestOfflineTraining:
    def test_dry_run_skips_when_not_enough_rows(self):
        svc = NeuralBaselineService(rows=_synthetic_rows(3), config=_LOW_CONFIG)
        result = svc.dry_run_train()
        assert result["trained"] is False
        assert "not_enough_data" in result["status"]

    def test_dry_run_trains_when_enough_rows(self):
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.dry_run_train()
        assert result["trained"] is True
        assert result["saved"] is False
        assert "metrics" in result
        assert result["metrics"]["accuracy"] >= 0.0
        assert result["metrics"]["brier_score"] >= 0.0

    def test_train_offline_returns_artifact(self):
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.train_offline()
        assert result["trained"] is True
        assert result["is_production"] is False
        artifact = result["artifact"]
        assert artifact["model_type"] == "neural_baseline_experimental"
        assert artifact["is_production"] is False
        assert "weights" in artifact

    def test_train_offline_artifact_is_not_production(self):
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.train_offline()
        assert result["artifact"]["is_production"] is False

    def test_train_offline_returns_not_enough_data_when_zero_rows(self):
        svc = NeuralBaselineService(rows=[], config=_LOW_CONFIG)
        result = svc.train_offline()
        assert result["trained"] is False
        assert result["status"] == "not_enough_data"


# ---------------------------------------------------------------------------
# 7. Evaluate offline
# ---------------------------------------------------------------------------

class TestEvaluateOffline:
    def _trained_artifact(self) -> dict:
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        return svc.train_offline()["artifact"]

    def test_evaluate_returns_metrics(self):
        artifact = self._trained_artifact()
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.evaluate_offline(artifact)
        assert result["status"] == "ok"
        m = result["metrics"]
        assert 0.0 <= m["accuracy"] <= 1.0
        assert m["brier_score"] >= 0.0
        assert m["cross_entropy"] >= 0.0
        assert "per_class" in m

    def test_evaluate_refuses_production_artifact(self):
        artifact = self._trained_artifact()
        artifact["is_production"] = True
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        with pytest.raises(ValueError, match="refuses production"):
            svc.evaluate_offline(artifact)

    def test_evaluate_returns_not_enough_data_when_no_rows(self):
        artifact = self._trained_artifact()
        svc = NeuralBaselineService(rows=[], config=_LOW_CONFIG)
        result = svc.evaluate_offline(artifact)
        assert result["status"] == "not_enough_data"


# ---------------------------------------------------------------------------
# 8. Compare against baseline
# ---------------------------------------------------------------------------

class TestCompareAgainstBaseline:
    def _trained_artifact(self) -> dict:
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        return svc.train_offline()["artifact"]

    def test_returns_comparison_dict(self):
        artifact = self._trained_artifact()
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.compare_against_baseline(artifact)
        assert result["status"] == "ok"
        assert "baseline" in result
        assert "neural" in result
        assert "brier_delta" in result
        assert "accuracy_delta" in result
        assert isinstance(result["neural_better_brier"], bool)
        assert isinstance(result["neural_better_accuracy"], bool)

    def test_baseline_metrics_use_stored_probs(self):
        # All rows have prob_home=0.6, prob_draw=0.25, prob_away=0.15
        # with actual_result="1" → baseline should predict "1" (argmax=0) → accuracy=1.0
        rows = [_make_row("1", prob_home=0.6, prob_draw=0.25, prob_away=0.15) for _ in range(10)]
        artifact = NeuralBaselineService(rows=rows, config=_LOW_CONFIG).train_offline()["artifact"]
        result = NeuralBaselineService(rows=rows, config=_LOW_CONFIG).compare_against_baseline(artifact)
        assert result["baseline"]["accuracy"] == 1.0

    def test_refuses_production_artifact(self):
        artifact = self._trained_artifact()
        artifact["is_production"] = True
        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        with pytest.raises(ValueError, match="refuses production"):
            svc.compare_against_baseline(artifact)


# ---------------------------------------------------------------------------
# 9 & 10. DB isolation — no production table mutations
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'neural_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


class TestDBIsolation:
    def test_train_offline_does_not_create_training_run_rows(self, db):
        from app.models.tables import ModelTrainingRunModel

        before = db.scalars(select(ModelTrainingRunModel)).all()
        assert len(before) == 0

        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        result = svc.train_offline()
        assert result["trained"] is True

        after = db.scalars(select(ModelTrainingRunModel)).all()
        assert len(after) == 0, "train_offline must not write to model_training_runs"

    def test_train_offline_does_not_touch_predictions(self, db):
        from app.models.tables import PredictionModel

        pred = PredictionModel(
            match_id=str(uuid4()),
            slate_id="slate-neural-guard",
            composition_hash="hash-guard",
            slate_version=1,
            generated_at=datetime.now(timezone.utc),
            home_probability=0.6,
            draw_probability=0.25,
            away_probability=0.15,
            recommended_outcome="1",
            confidence_band="medium",
            anchors_json="{}",
        )
        db.add(pred)
        db.commit()

        svc = NeuralBaselineService(rows=_synthetic_rows(10), config=_LOW_CONFIG)
        svc.train_offline()

        refreshed = db.scalars(select(PredictionModel)).all()
        assert len(refreshed) == 1
        assert refreshed[0].recommended_outcome == "1"
        assert refreshed[0].home_probability == 0.6


# ---------------------------------------------------------------------------
# 11. NumpyMLP internal sanity
# ---------------------------------------------------------------------------

class TestNumpyMLP:
    def test_forward_output_sums_to_one(self):
        mlp = _NumpyMLP(input_dim=15, hidden_dims=[8, 4], output_dim=3, seed=0)
        X = np.random.default_rng(0).standard_normal((10, 15)).astype(np.float32)
        probs = mlp.predict_proba(X)
        np.testing.assert_allclose(probs.sum(axis=1), np.ones(10), atol=1e-5)

    def test_fit_reduces_loss(self):
        rng = np.random.default_rng(42)
        X = rng.standard_normal((40, 15)).astype(np.float32)
        y = rng.integers(0, 3, size=40)
        mlp = _NumpyMLP(input_dim=15, hidden_dims=[8, 4], output_dim=3, seed=42)
        history = mlp.fit(X, y, epochs=50, lr=0.05, batch_size=20, seed=42)
        assert history[-1] <= history[0] + 0.1, "loss should not increase substantially"

    def test_to_dict_from_dict_roundtrip(self):
        mlp = _NumpyMLP(input_dim=15, hidden_dims=[8, 4], output_dim=3, seed=1)
        X = np.ones((5, 15), dtype=np.float32)
        before = mlp.predict_proba(X)
        d = mlp.to_dict()
        restored = _NumpyMLP.from_dict(d, input_dim=15, hidden_dims=[8, 4], output_dim=3)
        after = restored.predict_proba(X)
        np.testing.assert_allclose(before, after, atol=1e-6)
