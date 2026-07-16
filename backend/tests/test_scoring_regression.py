"""Regression baseline for the heuristic scoring engine.

Locks the current shape and numerical behavior of `_score_match_with_artifact`
against a fixed in-memory artifact and feature set so future refactors of the
Elo + Poisson + profile blend cannot silently move probabilities or Brier/log
loss without detection.

If the underlying engine intentionally changes its math (Fase 1: Dixon-Coles,
calibration), this baseline must be regenerated, the diff reviewed, and the
new metrics committed alongside the model change.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.services.model_training_artifacts import ModelTrainingArtifactsMixin
from app.services.model_training_service import ModelTrainingService


def _fixed_artifact() -> dict:
    """Hand-crafted artifact mimicking the heuristic_blend shape.

    Values are chosen so the home rating, scoring profile and league draw rate
    produce a meaningful probability vector that exercises the full blend
    (Elo + Poisson + team profile) without depending on any optional ML extras.
    """
    return {
        "model_type": "heuristic_blend",
        "ratings": {"Club A": 1600.0, "Club B": 1480.0},
        "offense": {"Club A": 1.6, "Club B": 1.1},
        "defense": {"Club A": 1.0, "Club B": 1.3},
        "competition_profiles": {
            "league": {
                "matches": 30.0,
                "home_wins": 14.0,
                "away_wins": 9.0,
                "draws": 7.0,
                "home_goals": 45.0,
                "away_goals": 30.0,
            }
        },
        "team_profiles": {
            "Club A": {
                "matches": 12.0,
                "points": 24.0,
                "goal_balance": 10.0,
                "goals_for": 22.0,
                "goals_against": 12.0,
                "draws": 3.0,
                "home_matches": 6.0,
                "home_points": 14.0,
                "home_goal_balance": 7.0,
                "home_goals_for": 13.0,
                "home_goals_against": 6.0,
                "home_draws": 1.0,
                "away_matches": 6.0,
                "away_points": 10.0,
                "away_goal_balance": 3.0,
                "away_goals_for": 9.0,
                "away_goals_against": 6.0,
                "away_draws": 2.0,
            },
            "Club B": {
                "matches": 12.0,
                "points": 14.0,
                "goal_balance": -3.0,
                "goals_for": 13.0,
                "goals_against": 16.0,
                "draws": 4.0,
                "home_matches": 6.0,
                "home_points": 9.0,
                "home_goal_balance": 0.0,
                "home_goals_for": 8.0,
                "home_goals_against": 8.0,
                "home_draws": 2.0,
                "away_matches": 6.0,
                "away_points": 5.0,
                "away_goal_balance": -3.0,
                "away_goals_for": 5.0,
                "away_goals_against": 8.0,
                "away_draws": 2.0,
            },
        },
        "league_draw_rate": 0.23,
        "blend_weights": {"elo": 0.4, "poisson": 0.25, "profile": 0.35},
        "class_priors": {"1": 0.46, "X": 0.24, "2": 0.30},
        "feature_names": ModelTrainingService.FEATURE_NAMES,
        "training_sample_size": 30,
        "model_name": ModelTrainingService.MODEL_NAME,
    }


def _make_match(competition_name: str = "league") -> SimpleNamespace:
    return SimpleNamespace(
        id="match-regression-1",
        home_team=SimpleNamespace(name="Club A"),
        away_team=SimpleNamespace(name="Club B"),
        competition=SimpleNamespace(name=competition_name),
    )


class _ScoringHarness(ModelTrainingArtifactsMixin):
    """Minimal concrete subclass exposing the scoring math for regression."""

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


@pytest.fixture(scope="module")
def baseline_scores() -> dict[str, float]:
    """Compute scoring once. Snapshot used by the regression tests below."""
    harness = _ScoringHarness()
    match = _make_match("league")
    return harness._score_match_with_artifact(match, _fixed_artifact())


def test_scoring_returns_normalized_probability_vector(baseline_scores: dict[str, float]) -> None:
    assert set(baseline_scores) == {"home", "draw", "away"}
    total = sum(baseline_scores.values())
    assert math.isclose(total, 1.0, abs_tol=0.01), f"probabilities must sum to ~1, got {total}"
    for label, value in baseline_scores.items():
        assert 0.0 <= value <= 1.0, f"{label} probability out of range: {value}"


def test_scoring_baseline_home_favored_with_stronger_rating(baseline_scores: dict[str, float]) -> None:
    """Club A is rated 120 Elo above Club B and has the stronger profile, so the
    engine must surface a clear home edge with the draw kept as the middle."""
    assert baseline_scores["home"] > baseline_scores["away"], (
        f"home should beat away with stronger rating + profile: {baseline_scores}"
    )
    assert baseline_scores["home"] > baseline_scores["draw"], (
        f"home should be the top class: {baseline_scores}"
    )


def test_scoring_baseline_snapshot(baseline_scores: dict[str, float]) -> None:
    """Locks the exact probabilities for the canonical artifact above.

    If this snapshot moves, the change must be intentional and the new
    expected values committed together with the engine change. This is the
    safety net the audit identified as missing (Hallazgo A5).

    Current baseline reflects the conditional blend (draw-dilution fix): the
    Dixon-Coles grid owns the draw mass and Elo/Poisson/profile vote only on
    the home-vs-away split of the remainder. With Club A clearly stronger
    than Club B (120 Elo edge + better profile) the home edge stays sharp and
    the draw is the untouched DC grid draw instead of a renormalized one."""
    expected = {"home": 0.671, "draw": 0.153, "away": 0.176}
    for label, expected_value in expected.items():
        actual = baseline_scores[label]
        assert math.isclose(actual, expected_value, abs_tol=0.02), (
            f"{label} drifted: expected ~{expected_value}, got {actual}. "
            f"If intentional, regenerate the snapshot."
        )


def test_blend_does_not_dilute_the_dixon_coles_draw() -> None:
    """Draw-dilution regression (market-comparison audit, 2026-07-16).

    Elo and the team profile carry no draw signal; when their full mass was
    blended into home/away and the vector renormalized, the DC draw shrank by
    a structural factor (~0.30 -> ~0.23 with default weights) on every match.
    For two identical teams the blended draw must now equal the DC grid draw
    (plus the bounded competition bias), and home/away must split the rest."""
    harness = _ScoringHarness()
    artifact = _fixed_artifact()
    # Perfectly symmetric opponents: same rating, same offense/defense, same
    # profile — any home edge comes only from the competition home bonus.
    artifact["ratings"] = {"Club A": 1500.0, "Club B": 1500.0}
    artifact["offense"] = {"Club A": 1.2, "Club B": 1.2}
    artifact["defense"] = {"Club A": 1.1, "Club B": 1.1}
    artifact["team_profiles"]["Club B"] = dict(artifact["team_profiles"]["Club A"])
    scored = harness._score_match_with_artifact(_make_match("league"), artifact)

    profile = artifact["competition_profiles"]["league"]
    home_lambda, away_lambda = harness._competition_lambda_priors(profile)
    rho = harness._dixon_coles_rho_from_draw_rate(
        max(min(float(artifact["league_draw_rate"]), 0.45), 0.18)
    )
    _, dc_draw, _ = harness._dixon_coles_outcome(
        home_lambda * 1.2 * 1.1, away_lambda * 1.2 * 1.1, rho
    )
    expected_draw = min(max(dc_draw, 0.08), 0.42)
    assert math.isclose(scored["draw"], expected_draw, abs_tol=0.005), (
        f"blended draw {scored['draw']} must match the DC grid draw {expected_draw} "
        f"(no renormalization dilution)"
    )
    assert math.isclose(sum(scored.values()), 1.0, abs_tol=0.01)


def test_dixon_coles_outcome_is_a_valid_distribution() -> None:
    """Mass of the bivariate Poisson grid should approximate 1 (within the
    grid truncation error) and the three outcomes must form a probability."""
    harness = _ScoringHarness()
    # Two equally strong teams: rho > 0 keeps low-score draw mass alive.
    p_home, p_draw, p_away = harness._dixon_coles_outcome(1.4, 1.2, rho=0.1)
    total = p_home + p_draw + p_away
    assert math.isclose(total, 1.0, abs_tol=1e-6), f"DC outcome must sum to 1, got {total}"
    for label, value in (("home", p_home), ("draw", p_draw), ("away", p_away)):
        assert 0.0 < value < 1.0, f"{label} probability out of range: {value}"
    # Home is slightly stronger (1.4 vs 1.2) and benefits from rho>0 reducing
    # 1-1 mass back toward draw -> still expect home edge over away.
    assert p_home > p_away, f"home should beat away with higher lambda: {p_home} vs {p_away}"


def test_dixon_coles_tau_corrects_low_scores() -> None:
    """tau adjusts only the four low-score cells; everywhere else it is 1."""
    harness = _ScoringHarness()
    home_lambda, away_lambda, rho = 1.3, 1.0, 0.15
    # Cells with no correction.
    assert harness._dixon_coles_tau(2, 3, home_lambda, away_lambda, rho) == 1.0
    assert harness._dixon_coles_tau(3, 1, home_lambda, away_lambda, rho) == 1.0
    # 0-0 gets deflated (negative correction) when rho > 0.
    assert harness._dixon_coles_tau(0, 0, home_lambda, away_lambda, rho) < 1.0
    # 1-1 gets deflated when rho > 0 (canonical DC behavior).
    assert harness._dixon_coles_tau(1, 1, home_lambda, away_lambda, rho) < 1.0
    # 1-0 and 0-1 get inflated to compensate.
    assert harness._dixon_coles_tau(1, 0, home_lambda, away_lambda, rho) > 1.0
    assert harness._dixon_coles_tau(0, 1, home_lambda, away_lambda, rho) > 1.0


def test_finalize_scores_applies_isotonic_when_curves_present() -> None:
    """F1.2 invariant: when the artifact carries calibration curves for the
    match's league, the final scores are routed through PAV rather than the
    class-priors blend."""
    harness = _ScoringHarness()
    artifact = _fixed_artifact()
    # League key for 'league' is just 'league' (no alias mapping in the test
    # fixture). The curves below: each class is a pure monotone passthrough
    # that doubles the raw probability, so calibration must visibly shift
    # the output relative to the priors-only blend.
    artifact["calibration_curves"] = {
        "league": {
            "1": [[0.0, 0.0], [1.0, 0.7]],  # raw 0.5 -> calibrated 0.35
            "X": [[0.0, 0.0], [1.0, 0.5]],  # raw 0.5 -> calibrated 0.25
            "2": [[0.0, 0.0], [1.0, 0.5]],  # raw 0.5 -> calibrated 0.25
        }
    }
    match = _make_match("league")
    calibrated = harness._finalize_scores(
        {"home": 0.5, "draw": 0.3, "away": 0.2}, artifact, match
    )
    total = sum(calibrated.values())
    assert math.isclose(total, 1.0, abs_tol=0.01)
    # Home class has the steeper curve (slope 0.7 vs 0.5 of the others), so
    # after calibration + renorm the home probability is amplified relative
    # to draw and away.
    assert calibrated["home"] > 0.5, calibrated
    assert calibrated["home"] > calibrated["draw"], calibrated


def test_finalize_scores_falls_back_to_priors_when_no_curve_for_league() -> None:
    """Without a curve covering the match's league, calibration must be
    skipped and the legacy blend-with-priors path used."""
    harness = _ScoringHarness()
    artifact = _fixed_artifact()
    # Curve exists, but for a different league than the match.
    artifact["calibration_curves"] = {
        "another-league": {"1": [[0.0, 0.0], [1.0, 1.0]], "X": [[0.0, 0.0], [1.0, 1.0]], "2": [[0.0, 0.0], [1.0, 1.0]]}
    }
    match = _make_match("league")
    raw_scores = {"home": 0.55, "draw": 0.25, "away": 0.20}
    finalized = harness._finalize_scores(raw_scores, artifact, match)
    # Without isotonic calibration the blend-with-priors path runs; the
    # result must equal what `_blend_with_priors` produces for the same
    # input.
    expected = harness._blend_with_priors(raw_scores, artifact)
    assert finalized == expected


def test_brier_and_log_loss_helpers_match_definitions() -> None:
    """The mixin computes Brier and log-loss internally. Locks the math so a
    refactor can't quietly switch the formula."""
    harness = _ScoringHarness()
    probabilities = [0.55, 0.22, 0.23]
    # Brier: sum of squared errors against one-hot target.
    expected_brier = (0.55 - 1.0) ** 2 + 0.22 ** 2 + 0.23 ** 2
    assert math.isclose(harness._brier_score(probabilities, 0), expected_brier, abs_tol=1e-6)
    # Log-loss on the true class.
    assert math.isclose(harness._log_loss(probabilities, 0), -math.log(0.55), abs_tol=1e-6)
