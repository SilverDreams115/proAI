"""Draw-calibrated coverage floor in the ticket optimizer.

When the (draw-calibrated) decision p_draw reaches the live-draw threshold,
X must end up covered (full lifted to a triple if neither doubles nor full
covered it), while simple ⊆ doubles ⊆ full stays intact and the simple pick
is never X.
"""
from __future__ import annotations

from app.domain.entities import Outcome

from tests.test_ticket_draw_coverage import (  # noqa: E402
    _build_recommendations,
    _picks,
    _prediction,
    _service,
)


def test_full_covers_x_when_calibrated_draw_is_live():
    # X is rank-3 (both home and away beat it) but p_draw >= live threshold
    # (0.25) — the floor must pull X into full coverage.
    preds = [
        _prediction("m1", position=1, home=0.40, draw=0.26, away=0.34, pick=Outcome.HOME, band="low"),
    ] + [
        _prediction(f"f{i}", position=i + 1, home=0.70, draw=0.18, away=0.12, pick=Outcome.HOME, band="high")
        for i in range(1, 14)
    ]
    recs = _build_recommendations(_service(), preds)
    m1 = next(r for r in recs if r.match_id == "m1")
    assert Outcome.DRAW in m1.decisions["full"].picks
    # simple is never the draw.
    assert m1.decisions["simple"].picks == [Outcome.HOME]
    # Monotonic: simple ⊆ doubles ⊆ full.
    s, d, f = (_picks(m1.decisions[k]) for k in ("simple", "doubles", "full"))
    assert s <= d <= f


def test_solid_favourite_low_draw_not_forced_to_cover_x():
    # p_draw well below the live threshold => no forced X coverage.
    preds = [
        _prediction("m1", position=1, home=0.80, draw=0.08, away=0.12, pick=Outcome.HOME, band="high"),
    ] + [
        _prediction(f"f{i}", position=i + 1, home=0.70, draw=0.18, away=0.12, pick=Outcome.HOME, band="high")
        for i in range(1, 14)
    ]
    recs = _build_recommendations(_service(), preds)
    m1 = next(r for r in recs if r.match_id == "m1")
    assert m1.decisions["full"].picks != []  # has a decision
    # Not forced to a triple by the draw floor (low p_draw).
    assert Outcome.DRAW not in m1.decisions["simple"].picks


def test_monotonic_preserved_across_slate_with_calibrated_draws():
    preds = [
        _prediction("a", position=1, home=0.38, draw=0.28, away=0.34, pick=Outcome.HOME, band="low"),
        _prediction("b", position=2, home=0.34, draw=0.30, away=0.36, pick=Outcome.AWAY, band="low"),
    ] + [
        _prediction(f"f{i}", position=i + 2, home=0.66, draw=0.20, away=0.14, pick=Outcome.HOME, band="medium")
        for i in range(1, 13)
    ]
    recs = _build_recommendations(_service(), preds)
    for r in recs:
        s, d, f = (_picks(r.decisions[k]) for k in ("simple", "doubles", "full"))
        assert s <= d <= f
