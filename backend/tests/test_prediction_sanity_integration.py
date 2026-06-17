"""End-to-end sanity wiring through PredictionService.

Proves the guardrail layer actually fires inside ``build_slate_predictions``
and that the response exposes the explicit, non-positional L/E/V fields the
UI consumes — closing the loop the unit tests open in test_sanity_layer.py.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.prediction_service import PredictionService


class _StubFeatureService:
    def __init__(self, feature_map: dict[str, float]) -> None:
        self._feature_map = feature_map

    def build_model_features(self, match, cutoff=None) -> dict[str, float]:
        return dict(self._feature_map)


class _StubTrainingService:
    """Returns an extreme away-favourite for an international friendly,
    mimicking the USA-vs-Australia / Panama-vs-Croatia symptom."""

    def __init__(self, scored: dict[str, float], *, friendly: bool, engine: str = "heuristic_blend") -> None:
        self._scored = scored
        self._friendly = friendly
        self._engine = engine
        self.training_repository = SimpleNamespace(session=object())

    def score_match(self, match) -> dict[str, float]:
        return dict(self._scored)

    def prediction_engine_for_match(self, match) -> str:
        return self._engine

    def competition_operating_policy(self, competition_name: str) -> dict[str, object]:
        if self._friendly:
            return {
                "competition_readiness": "ready",
                "live_pick_allowed": True,
                "policy_reason": "Operator-forced ready policy for friendlies.",
                "competition_key": "international-friendlies",
            }
        return {
            "competition_readiness": "ready",
            "live_pick_allowed": True,
            "policy_reason": "Historical benchmark passed.",
            "competition_key": "e0",
        }


def _build_slate(competition_name: str) -> object:
    match = SimpleNamespace(
        id="match-1",
        competition=SimpleNamespace(name=competition_name),
        home_team=SimpleNamespace(name="USA"),
        away_team=SimpleNamespace(name="Australia"),
        kickoff_at=SimpleNamespace(),
        evidence_items=[],
    )
    slate_match = SimpleNamespace(position=1, match=match)
    return SimpleNamespace(id="slate-friendly", matches=[slate_match])


# Thin national-team form: enough to anchor (band not blocked) but never
# HIGH evidence — exactly the case that was leaking 79% picks as "Listo".
_THIN_FRIENDLY_FEATURES = {
    "evidence_count": 0.0,
    "home_recent_matches": 3.0,
    "away_recent_matches": 3.0,
    "head_to_head_matches": 0.0,
    "form_gap": 0.4,
    "goal_balance_gap": 0.5,
    "rest_gap_days": 0.0,
}


def test_friendly_extreme_away_is_capped_flagged_and_reviewed() -> None:
    service = PredictionService(
        _StubTrainingService({"home": 0.13, "draw": 0.08, "away": 0.79}, friendly=True)
    )
    service.feature_service = _StubFeatureService(_THIN_FRIENDLY_FEATURES)

    response = service.build_slate_predictions(_build_slate("International Friendlies"))[0]

    # Explicit, non-positional L/E/V vector is present.
    assert set(response.probabilities) == {"L", "E", "V"}
    assert response.labels == {"L": "Local", "E": "Empate", "V": "Visitante"}
    # V (away) is still the top pick (identity preserved) but degraded.
    assert response.probabilities["V"] == max(response.probabilities.values())
    assert response.probabilities["V"] <= 0.65 + 1e-9
    # Raw is preserved for traceability.
    assert response.raw_probabilities["V"] >= 0.75
    # Flags and status reflect the guardrails.
    assert "INTERNATIONAL_FRIENDLY" in response.flags
    assert response.is_international_friendly is True
    assert response.final_status in {"REVISAR", "LISTO"}
    assert response.final_status != "FIJO"
    assert response.fallback_used is True


def test_friendly_low_evidence_cannot_be_fijo() -> None:
    service = PredictionService(
        _StubTrainingService({"home": 0.13, "draw": 0.08, "away": 0.79}, friendly=True)
    )
    # Insufficient data -> band blocked -> BLOQUEADO, definitely not FIJO.
    service.feature_service = _StubFeatureService(
        {
            "evidence_count": 0.0,
            "home_recent_matches": 0.0,
            "away_recent_matches": 0.0,
            "head_to_head_matches": 0.0,
        }
    )

    response = service.build_slate_predictions(_build_slate("International Friendlies"))[0]
    assert response.final_status in {"BLOQUEADO", "REVISAR"}
    assert response.final_status != "FIJO"


def test_local_label_tracks_home_team_end_to_end() -> None:
    """L must always be the home team's probability and V the away team's,
    regardless of which side the model favours."""
    service = PredictionService(
        _StubTrainingService({"home": 0.55, "draw": 0.25, "away": 0.20}, friendly=False, engine="xgboost")
    )
    service.feature_service = _StubFeatureService(
        {
            "evidence_count": 2.0,
            "home_recent_matches": 6.0,
            "away_recent_matches": 6.0,
            "head_to_head_matches": 5.0,
            "form_gap": 0.2,
            "goal_balance_gap": 0.3,
            "rest_gap_days": 0.0,
        }
    )

    response = service.build_slate_predictions(_build_slate("Premier League"))[0]
    # home_probability (legacy) and probabilities["L"] describe the same side.
    assert response.home_team_name == "USA"
    assert response.away_team_name == "Australia"
    assert response.recommended_outcome == "1"  # home favoured
    assert response.probabilities["L"] >= response.probabilities["V"]
    assert response.fallback_used is False
