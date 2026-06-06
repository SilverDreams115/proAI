"""Tests for the public backtest publisher (Fase 3.4)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services.model_training_service import ModelTrainingService


class _FakeSession:
    info: dict = {}

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakeTrainingRepo:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeEntityRepo:
    def __init__(self, matches: list) -> None:
        self._matches = matches

    def list_matches(self) -> list:
        return list(self._matches)


class _FakeResultRepo:
    def __init__(self, results_by_match: dict[str, list]) -> None:
        self._by_match = results_by_match

    def list_results_for_match(self, match_id: str) -> list:
        return list(self._by_match.get(match_id, []))

    def list_recent_team_results(self, _team_id: str, _before, limit: int = 8) -> list:
        return []

    def list_head_to_head_results_for_match(self, _match_id: str, limit: int = 5) -> list:
        return []


def _make_match(idx: int, competition: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"{competition}-{idx}",
        home_team_id=f"home-{competition}-{idx}",
        away_team_id=f"away-{competition}-{idx}",
        competition=SimpleNamespace(name=competition),
        home_team=SimpleNamespace(name=f"{competition} H{idx}", country="MX"),
        away_team=SimpleNamespace(name=f"{competition} A{idx}", country="MX"),
        kickoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=idx),
        evidence_items=[],
    )


def test_publish_backtest_writes_per_competition_files(tmp_path: Path, monkeypatch) -> None:
    """publish_backtest_history must emit one JSON per competition plus
    an index, never mixing leagues into a single file."""
    matches = [_make_match(i, "Liga MX") for i in range(8)] + [
        _make_match(i + 100, "Premier League") for i in range(8)
    ]
    results = {
        match.id: [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1",
                home_goals=2,
                away_goals=0,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id, away_team_id=match.away_team_id
                ),
            )
        ]
        for match in matches
    }
    service = ModelTrainingService(
        _FakeTrainingRepo(),
        _FakeEntityRepo(matches),
        _FakeResultRepo(results),
    )
    monkeypatch.setattr(
        service,
        "_build_heuristic_artifact",
        lambda prior_matches, result_lookup=None: {
            "model_type": "heuristic_blend",
            "class_priors": {"1": 0.55, "X": 0.2, "2": 0.25},
        },
    )
    monkeypatch.setattr(
        service,
        "_score_match_with_artifact",
        lambda match, artifact: {"home": 0.6, "draw": 0.2, "away": 0.2},
    )

    index = service.publish_backtest_history(output_dir=tmp_path, min_training_matches=2)

    # Index payload references each competition file present on disk.
    assert {entry["competition_key"] for entry in index["competitions"]} == {"mex", "e0"}
    for entry in index["competitions"]:
        file_path = tmp_path / entry["file"]
        assert file_path.is_file(), entry
        data = json.loads(file_path.read_text(encoding="utf-8"))
        # Every entry carries the full audit trail.
        assert data["entries"], f"empty entries for {entry['competition_key']}"
        for row in data["entries"]:
            assert {"match_id", "actual_result", "predicted_result", "probabilities", "hit", "brier"} <= row.keys()
        # Summary lines up with the entries.
        assert data["summary"]["matches_evaluated"] == len(data["entries"])
    # index.json exists.
    assert (tmp_path / "index.json").is_file()


def test_publish_backtest_records_hit_and_miss(tmp_path: Path, monkeypatch) -> None:
    """When the predicted top class is the actual result the entry is
    flagged `hit=True`; otherwise `False`. The summary reflects the count."""
    matches = [_make_match(i, "Liga MX") for i in range(5)]
    # Alternate actual results so half are wins, half are losses for the model.
    results: dict[str, list] = {}
    for i, match in enumerate(matches):
        results[match.id] = [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1" if i % 2 == 0 else "2",
                home_goals=1 if i % 2 == 0 else 0,
                away_goals=0 if i % 2 == 0 else 1,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id, away_team_id=match.away_team_id
                ),
            )
        ]
    service = ModelTrainingService(
        _FakeTrainingRepo(),
        _FakeEntityRepo(matches),
        _FakeResultRepo(results),
    )
    monkeypatch.setattr(
        service,
        "_build_heuristic_artifact",
        lambda prior_matches, result_lookup=None: {
            "model_type": "heuristic_blend",
            "class_priors": {"1": 0.5, "X": 0.25, "2": 0.25},
        },
    )
    # Model always predicts home, so it should hit only the "even-index"
    # matches above.
    monkeypatch.setattr(
        service,
        "_score_match_with_artifact",
        lambda match, artifact: {"home": 0.7, "draw": 0.2, "away": 0.1},
    )

    service.publish_backtest_history(output_dir=tmp_path, min_training_matches=1)

    data = json.loads((tmp_path / "mex.json").read_text(encoding="utf-8"))
    hits = sum(1 for row in data["entries"] if row["hit"])
    misses = sum(1 for row in data["entries"] if not row["hit"])
    assert hits + misses == data["summary"]["matches_evaluated"]
    # The model always says home wins; the hit count must equal the number
    # of even-index matches in the evaluated subset (i.e. half).
    assert hits > 0 and misses > 0, data["summary"]


def test_walk_forward_records_xgboost_alongside_heuristic(tmp_path: Path, monkeypatch) -> None:
    """When the prior window is large enough the walk-forward trains
    a booster, scores the held-out match with both engines, and the
    entry carries both signals so the summary can compare them."""
    n_matches = 35  # above XGBOOST_MIN_SAMPLE_SIZE (30)
    matches = [_make_match(i, "Liga MX") for i in range(n_matches)]
    results = {
        match.id: [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1" if i % 3 != 1 else "X",
                home_goals=1,
                away_goals=0,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id, away_team_id=match.away_team_id
                ),
            )
        ]
        for i, match in enumerate(matches)
    }
    service = ModelTrainingService(
        _FakeTrainingRepo(),
        _FakeEntityRepo(matches),
        _FakeResultRepo(results),
    )
    monkeypatch.setattr(
        service,
        "_build_heuristic_artifact",
        lambda prior_matches, result_lookup=None: {
            "model_type": "heuristic_blend",
            "class_priors": {"1": 0.55, "X": 0.2, "2": 0.25},
        },
    )

    # Stub BOTH engines so we can choose their accuracy and verify the
    # summary picks the right winner. The xgboost path is identified
    # by the artifact's model_type — that's how we route the scores.
    def fake_score(match, artifact):
        if artifact.get("model_type") == "xgboost_multiclass":
            # XGBoost stub: 90% on the actual result.
            actual = results[match.id][0].result_code
            tilt = {"1": "home", "X": "draw", "2": "away"}[actual]
            base = {"home": 0.05, "draw": 0.05, "away": 0.05}
            base[tilt] = 0.9
            return base
        # Heuristic stub: always picks home, neutral elsewhere — easy
        # to undershoot vs. the targeted xgboost.
        return {"home": 0.5, "draw": 0.3, "away": 0.2}

    monkeypatch.setattr(service, "_score_match_with_artifact", fake_score)

    # Skip the real booster training — just return a dummy artifact
    # with the xgboost model_type so the scorer routes correctly.
    monkeypatch.setattr(
        service,
        "_train_xgboost_artifact_for_backtest",
        lambda prior_matches, **kwargs: {
            "model_type": "xgboost_multiclass",
            "class_priors": {"1": 0.5, "X": 0.25, "2": 0.25},
            "booster_json": "{}",
        },
    )

    service.publish_backtest_history(output_dir=tmp_path, min_training_matches=2)
    data = json.loads((tmp_path / "mex.json").read_text(encoding="utf-8"))
    summary = data["summary"]

    # Heuristic remains at the top of the summary for back-compat.
    assert "hit_rate" in summary and "brier_score" in summary
    # Walk-forward adds a per-engine break-out.
    assert "heuristic" in summary and "xgboost" in summary
    assert summary["xgboost"]["matches_evaluated"] >= 1
    # With our stubs the XGBoost path beats heuristic; the summary
    # must flag that explicitly and the Brier delta is positive
    # (heuristic - xgboost > 0).
    assert summary["xgboost_beats_heuristic"] is True
    assert summary["brier_delta"] > 0
    # Per-entry the xgboost sub-object only appears once the prior
    # window crosses XGBOOST_MIN_SAMPLE_SIZE.
    xgb_entries = [row for row in data["entries"] if row.get("xgboost") is not None]
    assert xgb_entries, "expected some entries to carry the xgboost sub-object"
    for row in xgb_entries:
        assert {"predicted_result", "probabilities", "hit", "brier"} <= row["xgboost"].keys()


def test_walk_forward_flags_heuristic_winner_below_brier_margin(tmp_path: Path, monkeypatch) -> None:
    """If XGBoost matches the heuristic but doesn't beat it by the
    configured Brier margin, ``xgboost_beats_heuristic`` stays False
    so the production gate doesn't promote a non-improvement."""
    n_matches = 35
    matches = [_make_match(i, "Liga MX") for i in range(n_matches)]
    results = {
        match.id: [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1",
                home_goals=2,
                away_goals=0,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id, away_team_id=match.away_team_id
                ),
            )
        ]
        for match in matches
    }
    service = ModelTrainingService(
        _FakeTrainingRepo(),
        _FakeEntityRepo(matches),
        _FakeResultRepo(results),
    )
    monkeypatch.setattr(
        service,
        "_build_heuristic_artifact",
        lambda prior_matches, result_lookup=None: {"model_type": "heuristic_blend"},
    )
    # Both engines produce identical scores → Brier delta = 0 → flag stays False.
    monkeypatch.setattr(
        service,
        "_score_match_with_artifact",
        lambda match, artifact: {"home": 0.7, "draw": 0.2, "away": 0.1},
    )
    monkeypatch.setattr(
        service,
        "_train_xgboost_artifact_for_backtest",
        lambda prior_matches, **kwargs: {"model_type": "xgboost_multiclass", "booster_json": "{}"},
    )

    service.publish_backtest_history(output_dir=tmp_path, min_training_matches=2)
    summary = json.loads((tmp_path / "mex.json").read_text(encoding="utf-8"))["summary"]
    assert summary["xgboost_beats_heuristic"] is False
    assert summary["brier_delta"] == 0.0
