"""Score router must consult the walk-forward verdict and bypass the
XGBoost branch for competitions where heuristic won the backtest.

Why: Fase 2.6 walk-forward revealed XGBoost loses in every league with
data today. We can't quietly use a worse model in prod just because a
booster exists — the gate forces fallback to heuristic when the
publish-backtest index disqualified XGBoost for the match's league.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def _stub_match(competition_name: str = "Test League") -> SimpleNamespace:
    return SimpleNamespace(
        id="match-1",
        home_team=SimpleNamespace(name="Home", country="MX"),
        away_team=SimpleNamespace(name="Away", country="MX"),
        competition=SimpleNamespace(name=competition_name, country="MX", season="2026"),
        kickoff_at=None,
        evidence_items=[],
    )


def _write_verdict(tmp_path: Path, *, approved_keys: list[str], available: bool = True) -> Path:
    backtest_dir = tmp_path / "reports" / "backtest_history"
    backtest_dir.mkdir(parents=True, exist_ok=True)
    competitions = []
    if available:
        for key in approved_keys:
            competitions.append(
                {"competition_key": key, "xgboost_beats_heuristic": True}
            )
        # Always carry one disqualified league so the verdict is
        # genuinely a filter, not just an empty allowlist.
        competitions.append(
            {"competition_key": "blocked-league", "xgboost_beats_heuristic": False}
        )
    index_path = backtest_dir / "index.json"
    index_path.write_text(
        json.dumps({"model_name": "elo_poisson_blend", "competitions": competitions}),
        encoding="utf-8",
    )
    return index_path


def _isolate_verdict_paths(monkeypatch, tmp_path: Path) -> None:
    """Repoint the verdict-file lookup at the test tmpdir so the
    production /data/backtest_history/index.json — present whenever the
    test suite runs inside a deployed container — can't leak into the
    synthetic scenario. Without this, tests that expect "no verdict"
    silently see the live verdict and bypass the booster."""
    from app.services.model_training_service import ModelTrainingService

    monkeypatch.setattr(
        ModelTrainingService,
        "_XGBOOST_VERDICT_PATHS",
        (str(tmp_path / "reports" / "backtest_history" / "index.json"),),
    )


def _build_service():
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.services.model_training_service import ModelTrainingService

    class _FakeSession:
        info: dict = {}

    class _FakeTrainingRepo:
        def __init__(self) -> None:
            self.session = _FakeSession()

    return ModelTrainingService(
        _FakeTrainingRepo(),
        EntityRepository.__new__(EntityRepository),
        ResultRepository.__new__(ResultRepository),
    )


def test_score_bypasses_xgboost_for_disapproved_competition(tmp_path, monkeypatch) -> None:
    """Verdict says heuristic wins → score router must skip the booster
    and use the heuristic branch, even though the artifact carries a
    booster JSON."""
    monkeypatch.chdir(tmp_path)
    _isolate_verdict_paths(monkeypatch, tmp_path)
    _write_verdict(tmp_path, approved_keys=["other-league"])
    service = _build_service()
    service.reset_xgboost_verdict_cache()

    xgboost_calls: list[str] = []

    def fail_if_called(*args, **kwargs):
        xgboost_calls.append("called")
        return {"home": 0.99, "draw": 0.005, "away": 0.005}

    monkeypatch.setattr(service, "_score_with_xgboost", fail_if_called)
    # The heuristic branch needs at least these fields populated to
    # produce a sane probability vector; we stub a minimal artifact.
    artifact = {
        "model_type": "xgboost_multiclass",
        "feature_names": [],
        "booster_json": "{}",
        "ratings": {},
        "offense": {},
        "defense": {},
        "competition_profiles": {},
        "team_profiles": {},
        "league_draw_rate": 0.28,
        "blend_weights": {"elo": 0.4, "poisson": 0.25, "profile": 0.35},
        "class_priors": {"1": 0.45, "X": 0.28, "2": 0.27},
    }
    scored = service._score_match_with_artifact(_stub_match("Disapproved League"), artifact)
    # Heuristic branch was used: booster scoring never called.
    assert xgboost_calls == []
    # Probabilities are a real distribution from the heuristic blend.
    assert abs(sum(scored.values()) - 1.0) < 1e-6


def test_score_uses_xgboost_for_approved_competition(tmp_path, monkeypatch) -> None:
    """When the verdict explicitly approves the league the booster is
    used and its probabilities flow through finalization."""
    monkeypatch.chdir(tmp_path)
    _isolate_verdict_paths(monkeypatch, tmp_path)
    _write_verdict(tmp_path, approved_keys=["approved-league"])
    service = _build_service()
    service.reset_xgboost_verdict_cache()

    # Patch the alias map so "Approved League" normalises to the
    # exact key we approved.
    service.COMPETITION_ALIASES["approved-league"] = "approved-league"  # type: ignore[index]

    monkeypatch.setattr(
        service,
        "_score_with_xgboost",
        lambda match, artifact: {"home": 0.7, "draw": 0.2, "away": 0.1},
    )
    monkeypatch.setattr(
        service,
        "_finalize_scores",
        lambda scored, artifact, match: scored,
    )
    artifact = {
        "model_type": "xgboost_multiclass",
        "feature_names": [],
        "booster_json": "{}",
        "class_priors": {"1": 0.45, "X": 0.28, "2": 0.27},
    }
    scored = service._score_match_with_artifact(_stub_match("Approved League"), artifact)
    assert scored == {"home": 0.7, "draw": 0.2, "away": 0.1}


def test_score_uses_xgboost_when_no_verdict_file_present(tmp_path, monkeypatch) -> None:
    """Backwards compatible: with no published backtest the gate is
    effectively a no-op so existing behaviour is preserved."""
    monkeypatch.chdir(tmp_path)
    _isolate_verdict_paths(monkeypatch, tmp_path)
    # No verdict file written.
    service = _build_service()
    service.reset_xgboost_verdict_cache()

    monkeypatch.setattr(
        service,
        "_score_with_xgboost",
        lambda match, artifact: {"home": 0.6, "draw": 0.25, "away": 0.15},
    )
    monkeypatch.setattr(
        service,
        "_finalize_scores",
        lambda scored, artifact, match: scored,
    )
    artifact = {
        "model_type": "xgboost_multiclass",
        "feature_names": [],
        "booster_json": "{}",
        "class_priors": {"1": 0.45, "X": 0.28, "2": 0.27},
    }
    scored = service._score_match_with_artifact(_stub_match("Any League"), artifact)
    assert scored == {"home": 0.6, "draw": 0.25, "away": 0.15}
