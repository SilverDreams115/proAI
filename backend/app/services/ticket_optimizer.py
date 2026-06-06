"""Combinatorial optimizer for Progol ticket coverage (Fase 3.3).

Replaces the previous score-and-rank heuristic with an exact dynamic
program that maximizes the joint probability of getting every match
right, subject to the slate's budget of doubles and triples.

Formulation
-----------
For match `i` with sorted outcome probabilities (p1, p2, p3) we pick one
of three coverage decisions:

- Fixed:   covers the top outcome only.
           P(hit_i) = p1.
- Double:  covers the top two outcomes.
           P(hit_i) = p1 + p2. Costs 1 of the doubles budget.
- Triple:  covers all three outcomes.
           P(hit_i) = 1. Costs 1 of the triples budget.

Assuming match outcomes are independent — the same assumption Progol
itself makes in its grading — the joint probability of going clean is
the product over matches, so we optimize the sum of log-probabilities
instead. The DP state is `(match_index, doubles_used, triples_used)`,
and the optimum is reached by backtracking from the highest scoring
terminal state with `doubles_used <= max_doubles` and
`triples_used <= max_triples`.

Why this matters
----------------
The heuristic ranked doubles by an ad-hoc `double_score` mixing entropy
and gap, which only approximates EV. With the DP we always pick the
match where the second outcome contributes the most relative additional
hit probability — which is exactly what an EV-maximizing ticket needs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


_NEG_INF = float("-inf")
_PROB_FLOOR = 1e-6


@dataclass(frozen=True, slots=True)
class TicketOption:
    """One match's probability vector, already sorted by descending prob."""

    match_id: str
    top1: float
    top2: float
    top3: float


@dataclass(frozen=True, slots=True)
class TicketPlan:
    """Result of `optimize_ticket`: which decision per match plus EV."""

    decisions: dict[str, str]  # match_id -> "fixed" | "double" | "triple"
    log_expected_correct: float
    expected_correct: float


def optimize_ticket(
    options: list[TicketOption],
    *,
    max_doubles: int,
    max_triples: int,
) -> TicketPlan:
    """Return the assignment that maximizes the log-probability of going clean.

    Args:
        options: per-match probability vectors. The list order is preserved
            in the returned `decisions` mapping (keyed by `match_id`).
        max_doubles: hard cap on doubles used.
        max_triples: hard cap on triples used.

    Returns:
        `TicketPlan` with the chosen decision per match and the EV summary.
        Picks "fixed" everywhere when both budgets are zero.
    """
    if not options:
        return TicketPlan(decisions={}, log_expected_correct=0.0, expected_correct=1.0)

    max_doubles = max(0, int(max_doubles))
    max_triples = max(0, int(max_triples))

    # dp[i][d][t] = best log-prob considering first i matches with the budget
    # already consumed. choice[i][d][t] records the option used to reach the
    # state so we can backtrack the actual assignment afterwards.
    n = len(options)
    dp = [
        [[_NEG_INF] * (max_triples + 1) for _ in range(max_doubles + 1)]
        for _ in range(n + 1)
    ]
    choice: list[list[list[tuple[str, int, int] | None]]] = [
        [[None] * (max_triples + 1) for _ in range(max_doubles + 1)]
        for _ in range(n + 1)
    ]
    dp[0][0][0] = 0.0

    for i, opt in enumerate(options):
        # Pre-compute the log-gains so we never re-evaluate them inside the
        # inner loop. log(1) is always 0 for the triple case.
        gain_fixed = math.log(max(opt.top1, _PROB_FLOOR))
        gain_double = math.log(max(opt.top1 + opt.top2, _PROB_FLOOR))
        gain_triple = 0.0
        transitions = (
            ("fixed", 0, 0, gain_fixed),
            ("double", 1, 0, gain_double),
            ("triple", 0, 1, gain_triple),
        )
        for d in range(max_doubles + 1):
            for t in range(max_triples + 1):
                base = dp[i][d][t]
                if base == _NEG_INF:
                    continue
                for kind, dd, tt, gain in transitions:
                    nd = d + dd
                    nt = t + tt
                    if nd > max_doubles or nt > max_triples:
                        continue
                    candidate = base + gain
                    if candidate > dp[i + 1][nd][nt]:
                        dp[i + 1][nd][nt] = candidate
                        choice[i + 1][nd][nt] = (kind, d, t)

    # Pick the best terminal state — we do not require all budgets to be
    # fully consumed; the optimizer is free to leave doubles on the table
    # when none of the remaining matches benefit.
    best_d = best_t = 0
    best_value = _NEG_INF
    for d in range(max_doubles + 1):
        for t in range(max_triples + 1):
            value = dp[n][d][t]
            if value > best_value:
                best_value = value
                best_d, best_t = d, t

    if best_value == _NEG_INF:
        # Should never happen: the all-fixed path always exists.
        return TicketPlan(
            decisions={opt.match_id: "fixed" for opt in options},
            log_expected_correct=0.0,
            expected_correct=1.0,
        )

    decisions: dict[str, str] = {}
    d, t = best_d, best_t
    for i in range(n, 0, -1):
        recorded = choice[i][d][t]
        assert recorded is not None  # invariant: we only set best states via transitions
        kind, prev_d, prev_t = recorded
        decisions[options[i - 1].match_id] = kind
        d, t = prev_d, prev_t

    return TicketPlan(
        decisions=decisions,
        log_expected_correct=best_value,
        expected_correct=math.exp(best_value),
    )


def coverage_split(plan: TicketPlan) -> tuple[set[str], set[str]]:
    """Convenience: split a plan into (double_ids, triple_ids).

    Useful to keep the call sites in `TicketRecommendationService` short."""
    doubles = {match_id for match_id, kind in plan.decisions.items() if kind == "double"}
    triples = {match_id for match_id, kind in plan.decisions.items() if kind == "triple"}
    return doubles, triples


def optimize_for_coverage(
    options: list[TicketOption],
    *,
    max_doubles: int,
    max_triples: int,
    min_correct: int,
) -> "CoveragePlan":
    """Assign coverage to maximize `P(>= min_correct hits)` under a budget.

    Replaces the log-EV objective of `optimize_ticket` with the
    Poisson Binomial tail probability, which is the metric the Progol
    player actually cares about ("at least 13/14 correct"). The DP state
    is `(match_index, doubles_used, triples_used)` and carries the partial
    PMF over hit counts. At each match we try fixed / double / triple,
    extending the PMF by one Bernoulli trial.

    Args:
        options: per-match (sorted) probability vectors.
        max_doubles: cap on doubles.
        max_triples: cap on triples.
        min_correct: the floor we want the boleta to clear (e.g. 8 of 9,
            13 of 14).

    Returns:
        `CoveragePlan` with the chosen decision per match and the achieved
        `P(>= min_correct)`. Falls back to all-fixed when no budget is
        available."""
    if not options:
        return CoveragePlan(decisions={}, probability_target_met=1.0, expected_correct=0.0)

    max_doubles = max(0, int(max_doubles))
    max_triples = max(0, int(max_triples))

    n = len(options)
    # Sparse DP keyed by (match_index, doubles_used, triples_used) so the
    # type stays homogeneous and mypy can index it without conditional
    # narrowing. Memory cost remains tiny: at most n*(maxD+1)*(maxT+1)
    # entries, ~600 for a 14-match slate with K=8, M=4.
    dp: dict[tuple[int, int, int], list[float]] = {(0, 0, 0): [1.0]}
    choice: dict[tuple[int, int, int], tuple[str, int, int]] = {}

    for i, opt in enumerate(options):
        p_fixed = max(0.0, min(opt.top1, 1.0))
        p_double = max(0.0, min(opt.top1 + opt.top2, 1.0))
        transitions = (
            ("fixed", 0, 0, p_fixed),
            ("double", 1, 0, p_double),
            ("triple", 0, 1, 1.0),
        )
        for d in range(max_doubles + 1):
            for t in range(max_triples + 1):
                base = dp.get((i, d, t))
                if base is None:
                    continue
                for kind, dd, tt, p in transitions:
                    nd = d + dd
                    nt = t + tt
                    if nd > max_doubles or nt > max_triples:
                        continue
                    extended = _extend_pmf(base, p)
                    existing = dp.get((i + 1, nd, nt))
                    if existing is None or _prob_tail(extended, min_correct) > _prob_tail(existing, min_correct):
                        dp[(i + 1, nd, nt)] = extended
                        choice[(i + 1, nd, nt)] = (kind, d, t)

    best_d = best_t = 0
    best_prob = -1.0
    for d in range(max_doubles + 1):
        for t in range(max_triples + 1):
            final = dp.get((n, d, t))
            if final is None:
                continue
            tail = _prob_tail(final, min_correct)
            if tail > best_prob:
                best_prob = tail
                best_d, best_t = d, t
    terminal_pmf = dp.get((n, best_d, best_t))
    if terminal_pmf is None:
        return CoveragePlan(
            decisions={opt.match_id: "fixed" for opt in options},
            probability_target_met=0.0,
            expected_correct=0.0,
        )

    decisions: dict[str, str] = {}
    d, t = best_d, best_t
    for i in range(n, 0, -1):
        kind, prev_d, prev_t = choice[(i, d, t)]
        decisions[options[i - 1].match_id] = kind
        d, t = prev_d, prev_t

    expected = sum(k * value for k, value in enumerate(terminal_pmf))
    return CoveragePlan(
        decisions=decisions,
        probability_target_met=best_prob,
        expected_correct=expected,
    )


def min_budget_for_target(
    options: list[TicketOption],
    *,
    target_probability: float,
    min_correct: int,
    max_doubles_cap: int = 14,
    max_triples_cap: int = 14,
) -> "BudgetReport":
    """Find the smallest (doubles, triples) budget that reaches the target.

    Iterates in priority order: try with no triples first, growing
    doubles; if no budget of doubles alone gets us there, add triples
    one at a time. Returns the first plan whose
    `probability_target_met >= target_probability`, or the best
    achievable plan when even the full cap falls short."""
    best: CoveragePlan | None = None
    best_doubles = best_triples = 0
    for triples in range(max_triples_cap + 1):
        for doubles in range(max_doubles_cap + 1):
            plan = optimize_for_coverage(
                options,
                max_doubles=doubles,
                max_triples=triples,
                min_correct=min_correct,
            )
            if best is None or plan.probability_target_met > best.probability_target_met:
                best = plan
                best_doubles, best_triples = doubles, triples
            if plan.probability_target_met >= target_probability:
                return BudgetReport(
                    plan=plan,
                    doubles_needed=doubles,
                    triples_needed=triples,
                    target_reached=True,
                )
    return BudgetReport(
        plan=best if best is not None else CoveragePlan(decisions={}, probability_target_met=0.0, expected_correct=0.0),
        doubles_needed=best_doubles,
        triples_needed=best_triples,
        target_reached=False,
    )


@dataclass(frozen=True, slots=True)
class CoveragePlan:
    """Result of `optimize_for_coverage`: assignment + the P(>= K) reached."""

    decisions: dict[str, str]
    probability_target_met: float  # P(>= min_correct)
    expected_correct: float  # E[number of correct matches]


@dataclass(frozen=True, slots=True)
class BudgetReport:
    """Minimum-budget search result."""

    plan: CoveragePlan
    doubles_needed: int
    triples_needed: int
    target_reached: bool


def _extend_pmf(pmf: list[float], p: float) -> list[float]:
    """One Bernoulli convolution step: add a trial with hit prob `p`."""
    q = 1.0 - p
    new = [0.0] * (len(pmf) + 1)
    for k, value in enumerate(pmf):
        new[k] += value * q
        new[k + 1] += value * p
    return new


def _prob_tail(pmf: list[float], threshold: int) -> float:
    """P(successes >= threshold) given a finite PMF."""
    if threshold <= 0:
        return 1.0
    if threshold >= len(pmf):
        return 0.0
    return sum(pmf[threshold:])
