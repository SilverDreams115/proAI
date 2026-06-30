"""Knockout positions must never recommend a draw.

When a slate operator (or the upstream parser) flags a position as
knockout / final, the boleta semantics in that position don't allow
"X". The prediction service keeps the raw three probabilities so the
operator can see the model's full view, but the recommendation collapses
to L or V — whichever the model thinks is more likely.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "knockout.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_slate_match(session, *, is_knockout: bool):
    from app.models.tables import (
        CompetitionModel,
        MatchModel,
        ProgolSlateMatchModel,
        ProgolSlateModel,
        TeamModel,
    )

    comp = CompetitionModel(name="Champions League", country="Europe", season="2025-2026")
    home = TeamModel(name="PSG", country="FR")
    away = TeamModel(name="Arsenal", country="EN")
    session.add_all([comp, home, away])
    session.flush()
    kickoff = datetime(2026, 5, 31, 18, 0, tzinfo=timezone.utc)
    match = MatchModel(competition=comp, home_team=home, away_team=away, kickoff_at=kickoff)
    session.add(match)
    session.flush()
    slate = ProgolSlateModel(
        label="Test", draw_code="PG-TEST", week_type="weekend",
        registration_closes_at=kickoff - timedelta(hours=6), is_archived=False,
        composition_hash="hash-pgtest", slate_version=1,
    )
    session.add(slate)
    session.flush()
    session.add(
        ProgolSlateMatchModel(
            slate_id=slate.id, match_id=match.id, position=1, is_knockout=is_knockout
        )
    )
    session.flush()
    return slate


def test_recommendation_skips_draw_for_knockout_position(tmp_path) -> None:
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate = _seed_slate_match(session, is_knockout=True)
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        service = PredictionService(training)
        responses = service.build_slate_predictions(slate)
        assert len(responses) == 1
        response = responses[0]
        assert response.is_knockout is True
        # Pick collapses to L or V regardless of which class scored
        # highest before the knockout adjustment.
        assert response.recommended_outcome.value in {"1", "2"}
        assert any("Eliminatoria" in note for note in response.rationale)
    finally:
        invalidate_slate_prediction_cache()
        session.close()


def test_knockout_redistributes_draw_into_home_and_away(tmp_path) -> None:
    """Boleta rule: knockouts must report E=0% and redistribute the
    entire draw mass into L/V, proportional to their pre-adjustment
    share. The per-league shrinkage band is preserved only in the
    rationale note as a diagnostic of how aggressive the soft-model
    redistribution would have been."""
    from app.services.prediction_service import PredictionService

    service = PredictionService(training_service=None)

    home_p, draw_p, away_p = 0.30, 0.55, 0.15
    feature_map = {
        "home_goals_for_per_match": 1.8,
        "away_goals_for_per_match": 1.6,
        "home_goals_against_per_match": 1.1,
        "away_goals_against_per_match": 1.0,
        "home_recent_matches": 10,
        "away_recent_matches": 10,
    }

    new_home, new_draw, new_away, note = service._apply_knockout_adjustment(
        home_p, draw_p, away_p, feature_map,
    )

    assert new_draw == 0.0, "Draw must be exactly 0% on knockouts (boleta rule)."
    assert new_home > home_p
    assert new_away > away_p
    pre_ratio = home_p / away_p
    post_ratio = new_home / new_away
    # Home-bias direction must survive; with E redistributed
    # proportionally the L:V ratio is exactly preserved.
    assert post_ratio > 1.0 and pre_ratio > 1.0
    assert abs(post_ratio - pre_ratio) < 1e-9
    assert abs((new_home + new_draw + new_away) - 1.0) < 1e-6
    assert "Eliminatoria" in note
    assert "E=0%" in note


def test_knockout_thin_data_falls_back_to_minimum_shrinkage(tmp_path) -> None:
    """When neither team has recent results the shrinkage drops to the
    minimum so we don't pretend to know the right redistribution."""
    from app.services.prediction_service import PredictionService

    service = PredictionService(training_service=None)
    feature_map = {
        "home_goals_for_per_match": 0.0,
        "away_goals_for_per_match": 0.0,
        "home_goals_against_per_match": 0.0,
        "away_goals_against_per_match": 0.0,
        "home_recent_matches": 0,
        "away_recent_matches": 0,
    }
    new_home, new_draw, new_away, note = service._apply_knockout_adjustment(
        0.30, 0.55, 0.15, feature_map,
    )
    # Boleta rule supersedes data-anchored shrinkage: E=0% even when
    # no recent form is available. The diagnostic note still records
    # that we fell back to the band floor for the per-league summary.
    assert new_draw == 0.0
    assert "datos historicos limitados" in note


def test_knockout_shrinkage_calibration_widens_with_high_draw_league(tmp_path) -> None:
    """A league with a higher historical draw rate yields a larger
    shrinkage band — there's more draw mass to redistribute when the
    league naturally accepts 0-0s often."""
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService

    session = _make_session(tmp_path)
    try:
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        # Build a fake artifact: two competitions, one with high draw
        # rate (40%) and one low (15%).
        artifact = {
            "model_type": "heuristic_blend",
            "ratings": {},
            "offense": {},
            "defense": {},
            "competition_profiles": {
                "high-draw-league": {
                    "matches": 100, "draws": 40,
                    "home_wins": 30, "away_wins": 30,
                    "home_goals": 110, "away_goals": 100,
                },
                "low-draw-league": {
                    "matches": 100, "draws": 15,
                    "home_wins": 50, "away_wins": 35,
                    "home_goals": 160, "away_goals": 130,
                },
            },
            "team_profiles": {},
            "league_draw_rate": 0.275,
            "blend_weights": {"elo": 0.4, "poisson": 0.25, "profile": 0.35},
            "training_sample_size": 200,
            "feature_names": [],
        }
        # Monkeypatch latest_artifact to return our synthetic one.
        training.latest_artifact = lambda *a, **k: artifact  # type: ignore[assignment]

        hi_min, hi_max, hi_diag = training.knockout_shrinkage_bounds("High Draw League")
        lo_min, lo_max, lo_diag = training.knockout_shrinkage_bounds("Low Draw League")

        assert hi_diag["calibrated"] == 1.0
        assert lo_diag["calibrated"] == 1.0
        # High-draw league has more E mass to redistribute, so the
        # baseline shrinkage and the upper bound are both higher.
        assert hi_max > lo_max
        assert hi_diag["baseline"] > lo_diag["baseline"]
        # Low-draw league: when historical E is already below knockout
        # target the baseline collapses to 0 and the band stays narrow
        # at the floor.
        assert lo_diag["baseline"] == 0.0
        # Sanity: bounds stay inside the safety clamps.
        for band in (hi_min, hi_max, lo_min, lo_max):
            assert 0.0 <= band <= 0.95
    finally:
        session.close()


def test_knockout_calibration_falls_back_when_competition_has_no_history(tmp_path) -> None:
    """A new / placeholder competition with no profile in the artifact
    must use the conservative fallback band, not pretend to be
    calibrated."""
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService

    session = _make_session(tmp_path)
    try:
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        training.latest_artifact = lambda *a, **k: {  # type: ignore[assignment]
            "competition_profiles": {},
            "league_draw_rate": 0.28,
        }
        shrink_min, shrink_max, diag = training.knockout_shrinkage_bounds("Unknown League")
        assert diag["calibrated"] == 0.0
        assert (shrink_min, shrink_max) == (0.15, 0.55)
    finally:
        session.close()


def test_non_knockout_position_can_still_recommend_draw(tmp_path) -> None:
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import (
        PredictionService,
        invalidate_slate_prediction_cache,
    )

    invalidate_slate_prediction_cache()
    session = _make_session(tmp_path)
    try:
        slate = _seed_slate_match(session, is_knockout=False)
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        service = PredictionService(training)
        responses = service.build_slate_predictions(slate)
        response = responses[0]
        assert response.is_knockout is False
        # The "Eliminatoria" rationale note must NOT appear when the
        # position isn't a knockout.
        assert not any("Eliminatoria" in note for note in response.rationale)
    finally:
        invalidate_slate_prediction_cache()
        session.close()
