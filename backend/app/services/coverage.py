"""Poisson Binomial distribution for ticket coverage (Fase 5.1).

When every match in a slate has its own hit probability after coverage
(fixed → p1, double → p1+p2, triple → 1.0), the number of correct
matches follows a Poisson Binomial distribution. Unlike a binomial,
each Bernoulli trial has its own success probability.

Used by:
- `optimize_for_coverage` in ticket_optimizer.py to maximize
  `P(at least K correct)` under a budget of doubles/triples.
- API endpoint that reports `P(slate >= 8/9 correct)` so the operator
  sees the actual chance of a "clean enough" ticket.

We compute the full PMF in O(N^2) via the standard recurrence — N is at
most 14 in Progol, so this finishes in microseconds and stays
allocation-free.
"""
from __future__ import annotations


def poisson_binomial_pmf(probabilities: list[float]) -> list[float]:
    """Return the PMF of the number of successes for independent
    Bernoulli trials with the given probabilities.

    Args:
        probabilities: probability of success per trial. Each value is
            clipped to `[0, 1]` for numerical safety; an empty list
            returns `[1.0]` (one outcome: zero successes).

    Returns:
        `pmf[k]` = P(exactly k successes) for k = 0..N.
    """
    if not probabilities:
        return [1.0]
    pmf: list[float] = [1.0]
    for raw in probabilities:
        p = _clip_unit(float(raw))
        q = 1.0 - p
        next_pmf = [0.0] * (len(pmf) + 1)
        for k, value in enumerate(pmf):
            next_pmf[k] += value * q
            next_pmf[k + 1] += value * p
        pmf = next_pmf
    return pmf


def prob_at_least(probabilities: list[float], threshold: int) -> float:
    """Return `P(successes >= threshold)`.

    `threshold` is clamped: values <= 0 always return 1.0 (vacuous),
    values larger than N return 0.0. Equivalent to summing the tail of
    the PMF; we compute it directly to avoid building the full PMF
    twice."""
    if threshold <= 0:
        return 1.0
    n = len(probabilities)
    if threshold > n:
        return 0.0
    pmf = poisson_binomial_pmf(probabilities)
    return sum(pmf[threshold:])


def expected_successes(probabilities: list[float]) -> float:
    """E[number of successes] = sum of individual probabilities.

    Useful as a sanity-check companion to the PMF when reporting
    coverage stats."""
    return sum(_clip_unit(float(p)) for p in probabilities)


def _clip_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
