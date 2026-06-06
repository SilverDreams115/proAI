"""Tests for the structured scoring log line (Fase 4.3)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from app.services.model_training_artifacts import ModelTrainingArtifactsMixin
from app.services.model_training_service import ModelTrainingService


class _Harness(ModelTrainingArtifactsMixin):
    MODEL_NAME = ModelTrainingService.MODEL_NAME
    LOGISTIC_MIN_SAMPLE_SIZE = ModelTrainingService.LOGISTIC_MIN_SAMPLE_SIZE
    SMALL_SAMPLE_MAX_SIZE = ModelTrainingService.SMALL_SAMPLE_MAX_SIZE
    XGBOOST_MIN_SAMPLE_SIZE = ModelTrainingService.XGBOOST_MIN_SAMPLE_SIZE
    FEATURE_NAMES = ModelTrainingService.FEATURE_NAMES
    LABEL_TO_INDEX = ModelTrainingService.LABEL_TO_INDEX
    INDEX_TO_LABEL = ModelTrainingService.INDEX_TO_LABEL
    READY_HIT_RATE_THRESHOLD = ModelTrainingService.READY_HIT_RATE_THRESHOLD
    READY_BRIER_THRESHOLD = ModelTrainingService.READY_BRIER_THRESHOLD
    READY_CONFIDENT_HIT_RATE_THRESHOLD = ModelTrainingService.READY_CONFIDENT_HIT_RATE_THRESHOLD
    READY_MIN_CONFIDENT_PICKS = ModelTrainingService.READY_MIN_CONFIDENT_PICKS
    COMPETITION_ALIASES = ModelTrainingService.COMPETITION_ALIASES
    COMPETITION_POLICY_OVERRIDES = ModelTrainingService.COMPETITION_POLICY_OVERRIDES


def _make_match():
    return SimpleNamespace(
        id="match-scoring-log-1",
        home_team=SimpleNamespace(name="Club A"),
        away_team=SimpleNamespace(name="Club B"),
        competition=SimpleNamespace(name="Premier League"),
    )


def test_scoring_log_captures_engine_latency_and_probabilities(caplog) -> None:
    harness = _Harness()
    caplog.set_level(logging.INFO, logger="proai.scoring")
    harness._emit_scoring_log(
        match=_make_match(),
        engine="xgboost",
        raw={"home": 0.55, "draw": 0.25, "away": 0.20},
        final={"home": 0.58, "draw": 0.22, "away": 0.20},
        duration_seconds=0.012,
        artifact={
            "model_name": "elo_poisson_blend",
            "calibration_curves": {"e0": {"1": [[0.0, 0.0], [1.0, 1.0]]}},
        },
    )
    assert any(record.event == "match_scored" for record in caplog.records)
    record = next(rec for rec in caplog.records if rec.event == "match_scored")
    assert record.engine == "xgboost"
    assert record.competition_key == "e0"
    assert record.calibration_applied is True
    assert record.latency_ms > 0
    assert record.raw_probabilities == {"home": 0.55, "draw": 0.25, "away": 0.2}
    assert record.final_probabilities == {"home": 0.58, "draw": 0.22, "away": 0.2}


def test_scoring_log_flags_when_calibration_not_applied(caplog) -> None:
    harness = _Harness()
    caplog.set_level(logging.INFO, logger="proai.scoring")
    harness._emit_scoring_log(
        match=_make_match(),
        engine="heuristic_blend",
        raw={"home": 0.40, "draw": 0.30, "away": 0.30},
        final={"home": 0.40, "draw": 0.30, "away": 0.30},
        duration_seconds=0.001,
        artifact={"model_name": "elo_poisson_blend", "calibration_curves": {}},
    )
    record = next(rec for rec in caplog.records if rec.event == "match_scored")
    assert record.calibration_applied is False
