"""Every ticket this service emits has to fit on a real boleto.

The optimizer respects its own budget and the doubles-only ticket respects
its own, but `full` inherits the doubles the cheaper ticket covers — the
monotonic lift requires it — and keeps its triples on top. Adding two legal
compositions is not legal: PG-2346's conservative ticket reached 4 triples
and 4 doubles, 1,296 quinielas, against the 324 Lotería Nacional publishes.
"""
from __future__ import annotations

from app.domain.entities import Outcome
from app.domain.progol_pricing import combinations, is_legal_composition, limits_for

from tests.test_ticket_draw_coverage import (  # noqa: E402
    _build_recommendations,
    _pg2336_predictions,
    _prediction,
    _service,
)


def _composition(recs, mode: str) -> tuple[int, int]:
    doubles = sum(1 for r in recs if r.decisions[mode].pick_type == "double")
    triples = sum(1 for r in recs if r.decisions[mode].pick_type == "triple")
    return doubles, triples


def _assert_legal(recs, week_type: str) -> None:
    limits = limits_for(week_type)
    for mode in ("simple", "doubles", "full"):
        doubles, triples = _composition(recs, mode)
        assert is_legal_composition(week_type, doubles=doubles, triples=triples), (
            f"{mode}: {doubles} dobles + {triples} triples = "
            f"{combinations(doubles, triples)} quinielas, over {limits}"
        )


def test_weekend_slate_emits_only_playable_compositions():
    recs = _build_recommendations(_service(), _pg2336_predictions(), week_type="weekend")
    _assert_legal(recs, "weekend")


def test_midweek_slate_emits_only_playable_compositions():
    preds = [
        _prediction(
            f"m{i}", position=i, home=0.40, draw=0.27, away=0.33,
            pick=Outcome.HOME, band="low",
        )
        for i in range(1, 10)
    ]
    recs = _build_recommendations(_service(), preds, week_type="midweek")
    _assert_legal(recs, "midweek")


def test_uncertain_weekend_slate_stays_inside_the_table():
    """Fourteen coin-flips: every position wants coverage and the budget
    cannot buy it. The ticket has to come back inside the table anyway."""
    preds = [
        _prediction(
            f"m{i}", position=i, home=0.36, draw=0.32, away=0.32,
            pick=Outcome.HOME, band="low",
        )
        for i in range(1, 15)
    ]
    recs = _build_recommendations(_service(), preds, week_type="weekend")
    _assert_legal(recs, "weekend")


def test_demotion_keeps_the_most_likely_outcome():
    """A demoted position never loses its top pick — coverage is trimmed from
    the least likely outcome inwards, so the simple pick is untouched."""
    preds = [
        _prediction(
            f"m{i}", position=i, home=0.36, draw=0.32, away=0.32,
            pick=Outcome.HOME, band="low",
        )
        for i in range(1, 15)
    ]
    recs = _build_recommendations(_service(), preds, week_type="weekend")
    for rec in recs:
        for mode in ("simple", "doubles", "full"):
            assert Outcome.HOME in rec.decisions[mode].picks, f"pos {rec.position} {mode}"
