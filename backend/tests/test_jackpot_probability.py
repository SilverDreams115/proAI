"""Fase 5.2 — Honest jackpot probability on TicketCoverageMode.

The user's payout constraint is "Progol no paga 8/9" — only N/N pays
(midweek 9, weekend 14). These tests pin the new fields:

  * `jackpot_probability` == P(N/N correct) regardless of the
    coverage-target floor used internally.
  * `near_jackpot_probability` == P(>= N-1/N correct), labeled but not
    privileged in the math.
  * `tickets_for_half_chance` answers "how many independent boletas do
    I need to have ≥50% cumulative chance of one jackpot?"
"""
from __future__ import annotations

import math

from app.services.coverage import prob_at_least
from app.services.ticket_recommendation_service import TicketRecommendationService


def test_tickets_for_half_chance_matches_closed_form() -> None:
    """The closed form is ceil(log(0.5) / log(1 - p)). The function
    should match it for typical Progol probabilities."""
    for p in (0.001, 0.005, 0.01, 0.05, 0.1, 0.25):
        expected = math.ceil(math.log(0.5) / math.log(1.0 - p))
        assert TicketRecommendationService._tickets_for_half_chance(p) == expected


def test_tickets_for_half_chance_returns_none_for_edges() -> None:
    """p<=0 means jackpot is impossible (no number of boletas helps).
    p>=1 means it's already certain on a single ticket so the question
    is degenerate. Both cases return None so the UI can hide the row
    instead of showing nonsense numbers."""
    assert TicketRecommendationService._tickets_for_half_chance(0.0) is None
    assert TicketRecommendationService._tickets_for_half_chance(-0.1) is None
    assert TicketRecommendationService._tickets_for_half_chance(1.0) is None
    assert TicketRecommendationService._tickets_for_half_chance(1.5) is None


def test_jackpot_probability_uses_full_slate_size() -> None:
    """For a 14-partido weekend slate the jackpot must be computed at
    the FULL N (14), not at the 90% target floor (13). This is the
    "no paga 8/9" honesty requirement."""
    # 14 picks with p=0.6 each. P(14/14) = 0.6^14 ≈ 0.000784.
    probs = [0.6] * 14
    p14 = prob_at_least(probs, 14)
    assert math.isclose(p14, 0.6 ** 14, abs_tol=1e-9)
    # P(>=13/14) sums the two top PMF cells (k=13 and k=14).
    # P(k=13) = 14 * 0.4 * 0.6^13, P(k=14) = 0.6^14
    expected_near = 14 * 0.4 * (0.6 ** 13) + (0.6 ** 14)
    p13 = prob_at_least(probs, 13)
    assert math.isclose(p13, expected_near, abs_tol=1e-9)


def test_tickets_for_half_chance_realistic_progol_scale() -> None:
    """Sanity check at scale: with P(14/14) = 0.1%, you need ~693
    boletas for 50% cumulative chance. The user should see numbers in
    this order of magnitude — not "5 boletas" — when they ask "how
    many tickets for a real shot at the jackpot?"."""
    p = 0.001  # 0.1% per ticket
    k = TicketRecommendationService._tickets_for_half_chance(p)
    # Closed form: log(0.5)/log(0.999) ≈ 693
    assert k is not None
    assert 600 <= k <= 800
