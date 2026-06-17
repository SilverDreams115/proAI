"""Second-pass regression tests: the ticket optimizer must consume the
guardrailed DECISION probabilities, never the raw model output.

These fail against the pre-pass behaviour where `ticket_recommendation_service`
read `home/draw/away_probability` (raw) and could treat a friendly capped
from 0.81 → 0.60 as a strong 0.81 fixed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.entities import Outcome
from app.schemas.prediction import MatchPredictionResponse
from app.services.sanity_service import decision_leaks_raw_probabilities
from app.services.ticket_recommendation_service import TicketRecommendationService


def _service() -> TicketRecommendationService:
    return TicketRecommendationService(repository=None)  # type: ignore[arg-type]


def _prediction(
    *,
    raw: dict[str, float],
    decision: dict[str, float],
    final_status: str,
    flags: list[str],
    band: str = "high",
    pick: Outcome = Outcome.AWAY,
) -> MatchPredictionResponse:
    """Build a response where the legacy positional fields carry the
    DECISION (degraded) values — exactly as PredictionService now emits —
    while `raw_probabilities` preserves the original model output."""
    return MatchPredictionResponse(
        slate_id="slate-1",
        position=1,
        match_id="m1",
        competition_name="International Friendlies",
        home_team_name="USA",
        away_team_name="Australia",
        generated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        home_probability=decision["L"],
        draw_probability=decision["E"],
        away_probability=decision["V"],
        recommended_outcome=pick,
        competition_readiness="ready",
        live_pick_allowed=True,
        policy_reason="ok",
        confidence_band=band,
        rationale=[],
        probabilities=dict(decision),
        display_probabilities=dict(decision),
        decision_probabilities=dict(decision),
        raw_probabilities=dict(raw),
        evidence_level="low",
        confidence=0.1,
        risk_level="high",
        final_status=final_status,
        flags=list(flags),
        fallback_used=True,
        is_international_friendly=True,
    )


# --- Test 1: optimizer consumes decision, not raw --------------------------


def test_optimizer_uses_decision_not_raw_probabilities() -> None:
    service = _service()
    prediction = _prediction(
        raw={"L": 0.02, "E": 0.17, "V": 0.81},
        decision={"L": 0.20, "E": 0.20, "V": 0.60},
        final_status="REVISAR",
        flags=["LOW_EVIDENCE", "INTERNATIONAL_FRIENDLY"],
    )

    options = service._options_from_predictions([prediction])
    assert len(options) == 1
    # top1 must be the degraded 0.60, never the raw 0.81.
    assert options[0].top1 == 0.60
    assert options[0].top1 != 0.81

    best, _second, _third = service._sorted_outcomes(prediction)
    assert best[1] == 0.60


# --- Test 2: low evidence can never become a confident fixed ---------------


def test_low_evidence_cannot_become_confident_single() -> None:
    service = _service()
    # Even if the (degraded) top prob still cleared the 0.58 gate, the
    # LOW_EVIDENCE / REVISAR guardrail forbids the confident-single path.
    prediction = _prediction(
        raw={"L": 0.05, "E": 0.10, "V": 0.85},
        decision={"L": 0.18, "E": 0.22, "V": 0.60},
        final_status="REVISAR",
        flags=["LOW_EVIDENCE", "INTERNATIONAL_FRIENDLY"],
    )
    assert service._allows_confident_single(prediction) is False

    best, second, _third = service._sorted_outcomes(prediction)
    # No double_ids -> without the guardrail this could shortcut to fixed
    # if best>=0.58. The guardrail must keep it from the confident-single;
    # with no double budget it falls back to a plain fixed, but validation
    # must flag it high-risk.
    service._doubles_decision(prediction, best, second, double_ids=set())
    profile = service._risk_profile(prediction, {})
    validation = service._validation(profile)
    assert validation.level == "high"
    assert validation.label == "No dejar simple"
    assert profile["final_status"] == "REVISAR"
    # And when a double budget exists, the match takes the double.
    double = service._doubles_decision(prediction, best, second, double_ids={"m1"})
    assert double.pick_type == "double"


# --- Test 3: a degraded friendly changes the recommendation ----------------


def test_degraded_friendly_is_not_treated_as_strong_fixed() -> None:
    service = _service()
    # Pre-pass: raw V 0.81 + gap huge -> confident fixed. Post-pass: the
    # decision vector is 0.60 and the guardrail forbids the confident single.
    prediction = _prediction(
        raw={"L": 0.02, "E": 0.17, "V": 0.81},
        decision={"L": 0.20, "E": 0.20, "V": 0.60},
        final_status="REVISAR",
        flags=["LOW_EVIDENCE", "INTERNATIONAL_FRIENDLY", "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"],
    )
    best, second, _third = service._sorted_outcomes(prediction)
    decision = service._doubles_decision(prediction, best, second, double_ids={"m1"})
    assert decision.pick_type == "double"
    profile = service._risk_profile(prediction, {})
    assert profile["validation_risk"] > 0  # sanity risk applied
    assert service._validation(profile).level == "high"


# --- Test 4: legacy fields do not contradict the decision fields -----------


def test_legacy_fields_match_decision_no_contradiction() -> None:
    # PredictionService sets the legacy positional fields to the decision
    # values, so they must equal decision_probabilities and the optimizer
    # accessor must agree — no raw value can sneak through any field.
    prediction = _prediction(
        raw={"L": 0.02, "E": 0.17, "V": 0.81},
        decision={"L": 0.20, "E": 0.20, "V": 0.60},
        final_status="REVISAR",
        flags=["LOW_EVIDENCE"],
    )
    home, draw, away = prediction.decision_vector()
    assert (home, draw, away) == (0.20, 0.20, 0.60)
    assert prediction.home_probability == 0.20
    assert prediction.away_probability == 0.60
    # raw is preserved and is NOT what any decision path uses.
    assert prediction.raw_probabilities["V"] == 0.81
    assert not decision_leaks_raw_probabilities(
        prediction.raw_probabilities,
        prediction.display_probabilities,
        prediction.decision_probabilities,
    )


def test_leak_detector_fires_when_decision_tracks_raw() -> None:
    # Simulated regression: display degraded the raw, but decision still
    # equals raw -> the detector must catch it.
    raw = {"L": 0.02, "E": 0.17, "V": 0.81}
    display = {"L": 0.20, "E": 0.20, "V": 0.60}
    decision_bad = {"L": 0.02, "E": 0.17, "V": 0.81}
    assert decision_leaks_raw_probabilities(raw, display, decision_bad) is True
    decision_good = {"L": 0.20, "E": 0.20, "V": 0.60}
    assert decision_leaks_raw_probabilities(raw, display, decision_good) is False


def test_decision_vector_falls_back_to_legacy_when_unpopulated() -> None:
    # Hand-built fixtures that only set the legacy positional fields (no
    # decision_probabilities) must still work via the fallback.
    prediction = MatchPredictionResponse(
        slate_id="s",
        position=1,
        match_id="m",
        competition_name="Premier League",
        home_team_name="H",
        away_team_name="A",
        generated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        home_probability=0.55,
        draw_probability=0.25,
        away_probability=0.20,
        recommended_outcome=Outcome.HOME,
        competition_readiness="ready",
        live_pick_allowed=True,
        policy_reason="ok",
        confidence_band="high",
        rationale=[],
    )
    assert prediction.decision_vector() == (0.55, 0.25, 0.20)
