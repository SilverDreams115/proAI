import json
from types import SimpleNamespace

from app.services.feature_service import FeatureService
from app.services.model_training_service import ModelTrainingService
from app.services.prediction_service import PredictionService


class FakeSession:
    def __init__(self) -> None:
        self.info: dict[str, int] = {}
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeTrainingRepository:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.saved_runs: list[tuple[str, int, dict[str, object]]] = []

    def save_run(self, model_name: str, sample_size: int, artifact: dict[str, object]) -> None:
        self.saved_runs.append((model_name, sample_size, artifact))


class FakeEntityRepository:
    def __init__(self, matches: list[object] | None = None) -> None:
        self._matches = matches or []

    def list_matches(self) -> list[object]:
        return list(self._matches)


class FakeResultRepository:
    def __init__(self, results_by_match: dict[str, list[object]] | None = None) -> None:
        self._results_by_match = results_by_match or {}

    def list_results_for_match(self, match_id: str) -> list[object]:
        return list(self._results_by_match.get(match_id, []))

    def list_recent_team_results(self, team_id: str, before, limit: int = 8) -> list[object]:
        all_results: list[object] = []
        for results in self._results_by_match.values():
            all_results.extend(results)
        filtered = []
        for result in all_results:
            played_at = result.played_at
            if played_at >= before:
                continue
            match = result.match
            if match.home_team_id == team_id or match.away_team_id == team_id:
                filtered.append(result)
        filtered.sort(key=lambda item: item.played_at, reverse=True)
        return filtered[:limit]


class StubTrainingService:
    def __init__(self, scored: dict[str, float]) -> None:
        self._scored = scored
        self.training_repository = SimpleNamespace(session=object())

    def score_match(self, match) -> dict[str, float]:
        return dict(self._scored)

    def competition_operating_policy(self, competition_name: str) -> dict[str, object]:
        normalized = competition_name.strip().lower()
        if normalized == "premier league":
            return {
                "competition_readiness": "ready",
                "live_pick_allowed": True,
                "policy_reason": "Historical benchmark passed for Premier League regime.",
            }
        if normalized == "liga mx":
            return {
                "competition_readiness": "covered",
                "live_pick_allowed": False,
                "policy_reason": "Historical coverage exists for Liga MX; predictions are shown with caution.",
            }
        if normalized == "bundesliga":
            return {
                "competition_readiness": "not_ready",
                "live_pick_allowed": False,
                "policy_reason": "Historical benchmark failed for Bundesliga; live picks are blocked.",
            }
        return {
            "competition_readiness": "unclassified",
            "live_pick_allowed": False,
            "policy_reason": "Competition has no historical benchmark yet; live picks stay blocked.",
        }


class StubFeatureService:
    def __init__(self, feature_map: dict[str, float]) -> None:
        self._feature_map = feature_map

    def build_model_features(self, match, cutoff=None) -> dict[str, float]:
        return dict(self._feature_map)


def build_slate(competition_name: str = "Liga MX") -> object:
    match = SimpleNamespace(
        id="match-1",
        competition=SimpleNamespace(name=competition_name),
        home_team=SimpleNamespace(name="Club A"),
        away_team=SimpleNamespace(name="Club B"),
        kickoff_at=SimpleNamespace(),
        evidence_items=[object(), object()],
    )
    slate_match = SimpleNamespace(position=1, match=match)
    return SimpleNamespace(id="slate-1", matches=[slate_match])


def test_training_prefers_xgboost_artifact_when_available(monkeypatch) -> None:
    training_repository = FakeTrainingRepository()
    service = ModelTrainingService(
        training_repository,
        FakeEntityRepository(),
        FakeResultRepository(),
    )
    dataset = {"sample_size": 40, "classes_seen": [0, 1, 2], "rows": [], "labels": []}

    monkeypatch.setattr(service, "_build_training_dataset", lambda matches: dataset)
    monkeypatch.setattr(service, "_train_similarity_artifact", lambda current: None)
    monkeypatch.setattr(
        service,
        "_train_xgboost_artifact",
        lambda current: {"model_type": "xgboost_multiclass", "training_sample_size": 40},
    )

    artifact = service.train()

    assert artifact["model_type"] == "xgboost_multiclass"
    assert training_repository.saved_runs[0][2]["model_type"] == "xgboost_multiclass"


def test_prediction_context_penalizes_home_when_home_side_is_more_affected() -> None:
    service = PredictionService(StubTrainingService({"home": 0.4, "draw": 0.3, "away": 0.3}))
    service.feature_service = StubFeatureService(
        {
            "evidence_count": 2.0,
            "home_recent_matches": 3.0,
            "away_recent_matches": 3.0,
            "form_gap": 0.0,
            "goal_balance_gap": 0.0,
            "rest_gap_days": 0.0,
            "home_context_signal": 1.4,
            "away_context_signal": 0.2,
            "home_injury_signals": 2.0,
            "away_injury_signals": 0.0,
            "home_suspension_signals": 1.0,
            "away_suspension_signals": 0.0,
            "home_rotation_signals": 1.0,
            "away_rotation_signals": 0.0,
        }
    )

    response = service.build_slate_predictions(build_slate())[0]

    assert response.home_probability < 0.4
    assert response.away_probability > 0.3
    assert response.recommended_outcome == "2"
    assert response.live_pick_allowed is False
    assert response.competition_readiness == "covered"


def test_prediction_context_penalizes_away_when_away_side_is_more_affected() -> None:
    service = PredictionService(StubTrainingService({"home": 0.36, "draw": 0.3, "away": 0.34}))
    service.feature_service = StubFeatureService(
        {
            "evidence_count": 2.0,
            "home_recent_matches": 3.0,
            "away_recent_matches": 3.0,
            "form_gap": 0.0,
            "goal_balance_gap": 0.0,
            "rest_gap_days": 0.0,
            "home_context_signal": 0.1,
            "away_context_signal": 1.3,
            "home_injury_signals": 0.0,
            "away_injury_signals": 2.0,
            "home_suspension_signals": 0.0,
            "away_suspension_signals": 1.0,
            "home_rotation_signals": 0.0,
            "away_rotation_signals": 1.0,
        }
    )

    response = service.build_slate_predictions(build_slate())[0]

    assert response.home_probability > 0.36
    assert response.away_probability < 0.34
    assert response.recommended_outcome == "1"
    assert response.live_pick_allowed is False
    assert response.confidence_band != "blocked"


def test_prediction_direct_history_can_move_probabilities_without_availability() -> None:
    service = PredictionService(StubTrainingService({"home": 0.36, "draw": 0.3, "away": 0.34}))
    service.feature_service = StubFeatureService(
        {
            "evidence_count": 0.0,
            "form_gap": 0.0,
            "goal_balance_gap": 0.0,
            "rest_gap_days": 0.0,
            "head_to_head_matches": 2.0,
            "head_to_head_points_gap": 2.0,
            "head_to_head_goal_balance_gap": 1.0,
            "home_context_signal": 0.0,
            "away_context_signal": 0.0,
            "home_injury_signals": 0.0,
            "away_injury_signals": 0.0,
            "home_suspension_signals": 0.0,
            "away_suspension_signals": 0.0,
            "home_rotation_signals": 0.0,
            "away_rotation_signals": 0.0,
        }
    )

    response = service.build_slate_predictions(build_slate())[0]

    assert response.home_probability > 0.36
    assert response.away_probability < 0.34
    assert any("Historial directo" in note for note in response.rationale)


def test_feature_service_does_not_count_negated_availability_context() -> None:
    service = FeatureService(SimpleNamespace())
    match = SimpleNamespace(
        home_team_id="home-team",
        away_team_id="away-team",
        home_team=SimpleNamespace(name="Cruz Azul"),
        away_team=SimpleNamespace(name="Pumas"),
    )
    evidence = SimpleNamespace(
        summary="Cruz Azul vs Pumas context.",
        payload_json=json.dumps(
            {
                "teams": ["Cruz Azul", "Pumas"],
                "context_summary": (
                    "No se incluyen lesiones, suspendidos ni alineaciones "
                    "si no están confirmados por una fuente verificable."
                ),
            }
        ),
    )

    narrative = service._extract_narrative_signals(match, [evidence], [])

    assert narrative["injury_signal_total"] == 0.0
    assert narrative["suspension_signal_total"] == 0.0
    assert narrative["rotation_signal_total"] == 0.0
    assert narrative["home_injury_signals"] == 0.0
    assert narrative["away_suspension_signals"] == 0.0


def test_prediction_allows_live_pick_for_ready_competition() -> None:
    service = PredictionService(StubTrainingService({"home": 0.54, "draw": 0.23, "away": 0.23}))
    service.feature_service = StubFeatureService({"evidence_count": 2.0, "home_recent_matches": 3.0, "away_recent_matches": 3.0})

    response = service.build_slate_predictions(build_slate("Premier League"))[0]

    assert response.live_pick_allowed is True
    assert response.competition_readiness == "ready"
    assert response.confidence_band != "blocked"
    assert "Historical benchmark passed" in response.policy_reason


def test_prediction_blocks_live_pick_for_not_ready_competition() -> None:
    service = PredictionService(StubTrainingService({"home": 0.52, "draw": 0.22, "away": 0.26}))
    service.feature_service = StubFeatureService({"evidence_count": 2.0, "home_recent_matches": 3.0, "away_recent_matches": 3.0})

    response = service.build_slate_predictions(build_slate("Bundesliga"))[0]

    assert response.live_pick_allowed is False
    assert response.competition_readiness == "not_ready"
    assert response.confidence_band != "blocked"
    assert "blocked" in response.policy_reason.lower()


def test_prediction_blocks_when_no_data_anchors_the_match() -> None:
    """Fase 5.5 invariant: when the match has no recent form on either
    side and no head-to-head, the engine is extrapolating from training
    bias against a zero feature vector. The confidence band must drop to
    `blocked` so the UI does not present the hollow probabilities as
    grounded picks."""
    service = PredictionService(StubTrainingService({"home": 0.47, "draw": 0.28, "away": 0.25}))
    service.feature_service = StubFeatureService(
        {
            "evidence_count": 2.0,
            "home_recent_matches": 0.0,
            "away_recent_matches": 0.0,
            "head_to_head_matches": 0.0,
        }
    )

    response = service.build_slate_predictions(build_slate("Premier League"))[0]

    assert response.confidence_band == "blocked"
    assert any("ADVERTENCIA" in note and "sin anclaje" in note for note in response.rationale)


def test_prediction_keeps_scored_confidence_for_covered_competition() -> None:
    service = PredictionService(StubTrainingService({"home": 0.47, "draw": 0.28, "away": 0.25}))
    service.feature_service = StubFeatureService({"evidence_count": 2.0, "home_recent_matches": 3.0, "away_recent_matches": 3.0})

    response = service.build_slate_predictions(build_slate("Liga MX"))[0]

    assert response.live_pick_allowed is False
    assert response.competition_readiness == "covered"
    assert response.confidence_band != "blocked"


def test_walk_forward_evaluation_reports_ready_with_strong_historical_signal(monkeypatch) -> None:
    def make_match(index: int, days_offset: int, home_name: str, away_name: str) -> object:
        kickoff_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days_offset)
        return SimpleNamespace(
            id=f"match-{index}",
            home_team_id=f"home-{index}",
            away_team_id=f"away-{index}",
            competition=SimpleNamespace(name="League"),
            home_team=SimpleNamespace(name=home_name, country="MX"),
            away_team=SimpleNamespace(name=away_name, country="MX"),
            kickoff_at=kickoff_at,
            evidence_items=[],
        )

    from datetime import datetime, timedelta, timezone

    matches = [make_match(idx, idx, f"Club {idx}A", f"Club {idx}B") for idx in range(12)]
    results_by_match = {}
    for match in matches:
        results_by_match[match.id] = [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1",
                home_goals=2,
                away_goals=0,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    home_goals=2,
                    away_goals=0,
                ),
            )
        ]

    service = ModelTrainingService(
        FakeTrainingRepository(),
        FakeEntityRepository(matches),
        FakeResultRepository(results_by_match),
    )

    monkeypatch.setattr(
        service,
        "_build_training_artifact",
        lambda prior_matches, model_name: {"model_type": "heuristic_blend", "class_priors": {"1": 0.7, "X": 0.15, "2": 0.15}},
    )
    monkeypatch.setattr(
        service,
        "_score_match_with_artifact",
        lambda match, artifact: {"home": 0.62, "draw": 0.2, "away": 0.18},
    )

    evaluation = service.evaluate_walk_forward(min_training_matches=3, confidence_threshold=0.5)

    assert evaluation["matches_considered"] == 12
    assert evaluation["matches_evaluated"] == 9
    assert evaluation["hit_rate"] == 1.0
    assert evaluation["ready_for_live_picks"] is True
    assert evaluation["verdict"] == "ready"


def test_walk_forward_evaluation_reports_insufficient_data_when_history_is_too_short() -> None:
    from datetime import datetime, timezone

    match = SimpleNamespace(
        id="match-1",
        home_team_id="home-1",
        away_team_id="away-1",
        competition=SimpleNamespace(name="League"),
        home_team=SimpleNamespace(name="Club A", country="MX"),
        away_team=SimpleNamespace(name="Club B", country="MX"),
        kickoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        evidence_items=[],
    )
    result = SimpleNamespace(
        played_at=match.kickoff_at,
        result_code="1",
        home_goals=1,
        away_goals=0,
        match=SimpleNamespace(
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            home_goals=1,
            away_goals=0,
        ),
    )
    service = ModelTrainingService(
        FakeTrainingRepository(),
        FakeEntityRepository([match]),
        FakeResultRepository({match.id: [result]}),
    )

    evaluation = service.evaluate_walk_forward(min_training_matches=3, confidence_threshold=0.5)

    assert evaluation["matches_evaluated"] == 0
    assert evaluation["ready_for_live_picks"] is False
    assert evaluation["verdict"] == "insufficient_data"


def test_competition_walk_forward_reports_per_competition(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    def make_match(index: int, competition_name: str) -> object:
        kickoff_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
        return SimpleNamespace(
            id=f"{competition_name}-{index}",
            home_team_id=f"home-{competition_name}-{index}",
            away_team_id=f"away-{competition_name}-{index}",
            competition=SimpleNamespace(name=competition_name),
            home_team=SimpleNamespace(name=f"{competition_name} Home {index}", country="MX"),
            away_team=SimpleNamespace(name=f"{competition_name} Away {index}", country="MX"),
            kickoff_at=kickoff_at,
            evidence_items=[],
        )

    matches = [make_match(index, "Liga MX") for index in range(4)]
    matches.extend(make_match(index + 10, "Premier League") for index in range(4))
    results_by_match = {
        match.id: [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1",
                home_goals=2,
                away_goals=0,
                match=SimpleNamespace(
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                ),
            )
        ]
        for match in matches
    }
    service = ModelTrainingService(
        FakeTrainingRepository(),
        FakeEntityRepository(matches),
        FakeResultRepository(results_by_match),
    )
    monkeypatch.setattr(
        service,
        "_build_heuristic_artifact",
        lambda prior_matches, result_lookup=None: {
            "model_type": "heuristic_blend",
            "class_priors": {"1": 0.7, "X": 0.15, "2": 0.15},
        },
    )
    monkeypatch.setattr(
        service,
        "_score_match_with_artifact",
        lambda match, artifact: {"home": 0.62, "draw": 0.2, "away": 0.18},
    )

    evaluation = service.evaluate_competitions_walk_forward(min_training_matches=1)

    assert evaluation["evaluation_mode"] == "walk_forward_by_competition"
    assert evaluation["competitions_considered"] == 2
    competition_keys = {item["competition_key"] for item in evaluation["competitions"]}
    assert competition_keys == {"mex", "e0"}
    assert all(item["matches_evaluated"] == 3 for item in evaluation["competitions"])


def test_walk_forward_does_not_cross_train_between_leagues(monkeypatch) -> None:
    """F1.3 invariant: when predicting a Premier League match, the artifact
    must be built only from prior Premier League matches — not from prior
    Liga MX matches that happen to be earlier on the global timeline.

    Captures the artifact's training input on every iteration and checks
    that every artifact only saw matches from the same competition as the
    target match."""
    from datetime import datetime, timedelta, timezone

    def make_match(index: int, competition_name: str) -> object:
        kickoff_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
        return SimpleNamespace(
            id=f"{competition_name}-{index}",
            home_team_id=f"home-{competition_name}-{index}",
            away_team_id=f"away-{competition_name}-{index}",
            competition=SimpleNamespace(name=competition_name),
            home_team=SimpleNamespace(name=f"{competition_name} Home {index}", country="MX"),
            away_team=SimpleNamespace(name=f"{competition_name} Away {index}", country="MX"),
            kickoff_at=kickoff_at,
            evidence_items=[],
        )

    # Interleave the two leagues on the global timeline so that — under the
    # old global walk-forward — Premier League matches would have Liga MX
    # priors and vice versa.
    matches: list[object] = []
    for index in range(6):
        matches.append(make_match(index * 2, "Liga MX"))
        matches.append(make_match(index * 2 + 1, "Premier League"))
    results_by_match = {
        match.id: [
            SimpleNamespace(
                played_at=match.kickoff_at,
                result_code="1",
                home_goals=2,
                away_goals=0,
                match=SimpleNamespace(home_team_id=match.home_team_id, away_team_id=match.away_team_id),
            )
        ]
        for match in matches
    }
    service = ModelTrainingService(
        FakeTrainingRepository(),
        FakeEntityRepository(matches),
        FakeResultRepository(results_by_match),
    )

    captured_artifact_inputs: list[tuple[str, set[str]]] = []

    def build_with_capture(prior_matches, result_lookup=None):
        seen_leagues = {m.competition.name for m in prior_matches}
        # Tag a target placeholder so the verification picks it up; the
        # actual artifact content is irrelevant for this test.
        captured_artifact_inputs.append(("artifact_built", seen_leagues))
        return {"model_type": "heuristic_blend", "class_priors": {"1": 0.5, "X": 0.25, "2": 0.25}}

    def score_with_capture(match, artifact):
        # Pair the most recent capture with the target match so we can
        # verify the artifact's training data matched the target league.
        if captured_artifact_inputs:
            tag, leagues = captured_artifact_inputs[-1]
            captured_artifact_inputs[-1] = (match.competition.name, leagues)
        return {"home": 0.5, "draw": 0.25, "away": 0.25}

    monkeypatch.setattr(service, "_build_heuristic_artifact", build_with_capture)
    monkeypatch.setattr(service, "_score_match_with_artifact", score_with_capture)

    evaluation = service.evaluate_walk_forward(min_training_matches=2)

    # Sanity: walk-forward produced evaluations.
    assert evaluation["matches_evaluated"] > 0
    assert captured_artifact_inputs, "expected at least one artifact build"

    # Invariant: every artifact's training leagues == the target league.
    for target_league, seen_leagues in captured_artifact_inputs:
        assert target_league != "artifact_built", "every capture must be tagged with a target league"
        assert seen_leagues == {target_league}, (
            f"artifact for {target_league} match leaked priors from {seen_leagues - {target_league}}"
        )
