"""Ticket coverage monotonicity + draw-risk reporting.

Covers the two combinatorial/reporting fixes from the draw-coverage audit:

* Coverage nesting invariant simple ⊆ doubles ⊆ full. The full ticket is
  the most aggressive mode and must never cover LESS than a cheaper one.
  Regression cases observed in PG-2336: pos13 England–Croatia (doubles=2X,
  full=2) and pos9 Sweden–Tunisia (doubles=1X, full=1).
* draw_risk projection: p_draw, draw_rank, empate vivo (>=0.25) / fuerte
  (>=0.30) flags, and per-mode X coverage — reporting only, never alters
  probabilities or picks.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.entities import Outcome
from app.schemas.prediction import MatchPredictionResponse, TicketDecisionResponse
from app.services.ticket_recommendation_service import TicketRecommendationService


def _service() -> TicketRecommendationService:
    # Helper methods under test never touch the repository, so a None
    # repository is sufficient for these pure-logic checks.
    return TicketRecommendationService(repository=None)  # type: ignore[arg-type]


def _prediction(
    match_id: str,
    *,
    position: int,
    home: float,
    draw: float,
    away: float,
    pick: Outcome,
    band: str = "low",
) -> MatchPredictionResponse:
    return MatchPredictionResponse(
        slate_id="slate-1",
        position=position,
        match_id=match_id,
        competition_name="International Friendlies",
        home_team_name="H",
        away_team_name="A",
        generated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        home_probability=home,
        draw_probability=draw,
        away_probability=away,
        recommended_outcome=pick,
        competition_readiness="ready",
        live_pick_allowed=True,
        policy_reason="ok",
        confidence_band=band,
        rationale=[],
    )


def _picks(decision: TicketDecisionResponse) -> set[str]:
    return {outcome.value for outcome in decision.picks}


# PG-2336 weekend slate (the one that exhibited the bug), 14 matches.
_PG2336 = [
    ("m1", 0.61, 0.25, 0.14, Outcome.HOME, "high"),
    ("m2", 0.22, 0.27, 0.51, Outcome.AWAY, "blocked"),
    ("m3", 0.82, 0.16, 0.02, Outcome.HOME, "blocked"),
    ("m4", 0.45, 0.24, 0.31, Outcome.HOME, "low"),
    ("m5", 0.02, 0.19, 0.80, Outcome.AWAY, "low"),
    ("m6", 0.78, 0.20, 0.02, Outcome.HOME, "blocked"),
    ("m7", 0.42, 0.23, 0.35, Outcome.HOME, "low"),
    ("m8", 0.77, 0.21, 0.02, Outcome.HOME, "low"),
    ("m9", 0.59, 0.25, 0.16, Outcome.HOME, "low"),
    ("m10", 0.41, 0.27, 0.32, Outcome.HOME, "blocked"),
    ("m11", 0.67, 0.17, 0.15, Outcome.HOME, "high"),
    ("m12", 0.45, 0.24, 0.30, Outcome.HOME, "low"),
    ("m13", 0.03, 0.33, 0.64, Outcome.AWAY, "low"),
    ("m14", 0.52, 0.12, 0.37, Outcome.HOME, "medium"),
]


def _pg2336_predictions() -> list[MatchPredictionResponse]:
    return [
        _prediction(mid, position=i + 1, home=h, draw=d, away=a, pick=pk, band=b)
        for i, (mid, h, d, a, pk, b) in enumerate(_PG2336)
    ]


def _build_recommendations(service: TicketRecommendationService, predictions):
    """Run the real pre-persistence pipeline (no DB writes)."""
    rule = service._rule_for_slate("weekend", len(predictions))
    profiles = {p.match_id: service._risk_profile(p, {}) for p in predictions}
    double_ids = service._choose_doubles(predictions, profiles, rule["doubles_only_max"])
    full_double_ids, full_triple_ids = service._choose_full_coverage(
        predictions, profiles, rule
    )
    return [
        service._build_match_recommendation(
            prediction=p,
            profile=profiles[p.match_id],
            double_ids=double_ids,
            full_double_ids=full_double_ids,
            full_triple_ids=full_triple_ids,
        )
        for p in predictions
    ]


def test_full_is_superset_of_doubles_is_superset_of_simple() -> None:
    service = _service()
    recs = _build_recommendations(service, _pg2336_predictions())
    assert len(recs) == 14
    for rec in recs:
        simple = _picks(rec.decisions["simple"])
        doubles = _picks(rec.decisions["doubles"])
        full = _picks(rec.decisions["full"])
        assert simple <= doubles, f"pos {rec.position}: doubles must contain simple"
        assert doubles <= full, f"pos {rec.position}: full must contain doubles"


def test_full_keeps_x_when_doubles_covers_x() -> None:
    service = _service()
    recs = _build_recommendations(service, _pg2336_predictions())
    for rec in recs:
        doubles = _picks(rec.decisions["doubles"])
        full = _picks(rec.decisions["full"])
        if "X" in doubles:
            assert "X" in full, f"pos {rec.position}: full dropped X that doubles covered"


def test_pos13_like_case_full_cannot_be_less_than_doubles() -> None:
    """Direct regression: force doubles=2X, full=fixed; fix must lift full to 2X."""
    service = _service()
    pred = _prediction("m13", position=13, home=0.03, draw=0.33, away=0.64, pick=Outcome.AWAY)
    profile = service._risk_profile(pred, {})
    rec = service._build_match_recommendation(
        prediction=pred,
        profile=profile,
        double_ids={"m13"},          # doubles → [away, draw] = 2X
        full_double_ids=set(),       # full would have been fixed → 2
        full_triple_ids=set(),
    )
    assert _picks(rec.decisions["doubles"]) == {"2", "X"}
    # Monotonicity fix: full must cover at least what doubles covers.
    assert _picks(rec.decisions["full"]) >= {"2", "X"}


def test_pos9_like_case_full_cannot_drop_x() -> None:
    service = _service()
    pred = _prediction("m9", position=9, home=0.59, draw=0.25, away=0.16, pick=Outcome.HOME)
    profile = service._risk_profile(pred, {})
    rec = service._build_match_recommendation(
        prediction=pred,
        profile=profile,
        double_ids={"m9"},           # doubles → [home, draw] = 1X
        full_double_ids=set(),
        full_triple_ids=set(),
    )
    assert _picks(rec.decisions["doubles"]) == {"1", "X"}
    assert _picks(rec.decisions["full"]) >= {"1", "X"}


def test_monotonicity_lift_does_not_remove_triple_coverage() -> None:
    """If full was already a triple, it must stay a full triple."""
    service = _service()
    pred = _prediction("m", position=1, home=0.40, draw=0.33, away=0.27, pick=Outcome.HOME)
    profile = service._risk_profile(pred, {})
    rec = service._build_match_recommendation(
        prediction=pred,
        profile=profile,
        double_ids={"m"},
        full_double_ids=set(),
        full_triple_ids={"m"},       # full → triple
    )
    assert _picks(rec.decisions["full"]) == {"1", "X", "2"}


def test_draw_risk_rank_and_flags() -> None:
    service = _service()
    # p_draw = 0.33, draw is 2nd behind away (0.64).
    pred = _prediction("m13", position=13, home=0.03, draw=0.33, away=0.64, pick=Outcome.AWAY)
    rec = service._build_match_recommendation(
        prediction=pred,
        profile=service._risk_profile(pred, {}),
        double_ids={"m13"},
        full_double_ids=set(),
        full_triple_ids=set(),
    )
    risk = rec.draw_risk
    assert risk is not None
    assert risk.p_draw == pytest.approx(0.33)
    assert risk.draw_rank == 2
    assert risk.is_live_draw is True
    assert risk.is_strong_draw is True
    # doubles covers X; full lifted to also cover X; simple does not.
    assert risk.covered_simple is False
    assert risk.covered_doubles is True
    assert risk.covered_full is True


def test_draw_risk_live_threshold_boundary() -> None:
    service = _service()
    # p_draw exactly 0.25 → empate vivo, not fuerte.
    live = _prediction("a", position=1, home=0.50, draw=0.25, away=0.25, pick=Outcome.HOME)
    rec_live = service._build_match_recommendation(
        prediction=live, profile=service._risk_profile(live, {}),
        double_ids=set(), full_double_ids=set(), full_triple_ids=set(),
    )
    assert rec_live.draw_risk is not None
    assert rec_live.draw_risk.is_live_draw is True
    assert rec_live.draw_risk.is_strong_draw is False

    # p_draw 0.24 → below the live threshold.
    cold = _prediction("b", position=2, home=0.52, draw=0.24, away=0.24, pick=Outcome.HOME)
    rec_cold = service._build_match_recommendation(
        prediction=cold, profile=service._risk_profile(cold, {}),
        double_ids=set(), full_double_ids=set(), full_triple_ids=set(),
    )
    assert rec_cold.draw_risk is not None
    assert rec_cold.draw_risk.is_live_draw is False


def test_draw_risk_third_rank_when_draw_is_least_likely() -> None:
    service = _service()
    pred = _prediction("m14", position=14, home=0.52, draw=0.12, away=0.37, pick=Outcome.HOME)
    rec = service._build_match_recommendation(
        prediction=pred, profile=service._risk_profile(pred, {}),
        double_ids=set(), full_double_ids=set(), full_triple_ids=set(),
    )
    assert rec.draw_risk is not None
    assert rec.draw_risk.draw_rank == 3
    assert rec.draw_risk.is_live_draw is False
