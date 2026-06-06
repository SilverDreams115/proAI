"""Tests for the combinatorial ticket optimizer (Fase 3.3)."""
from __future__ import annotations

import itertools
import math

import pytest

from app.services.ticket_optimizer import TicketOption, coverage_split, optimize_ticket


def _option(match_id: str, top1: float, top2: float, top3: float | None = None) -> TicketOption:
    """Build an option, defaulting top3 so the three probabilities still sum to 1."""
    if top3 is None:
        top3 = max(0.0, 1.0 - top1 - top2)
    return TicketOption(match_id=match_id, top1=top1, top2=top2, top3=top3)


def _brute_force_best(
    options: list[TicketOption], max_doubles: int, max_triples: int
) -> tuple[dict[str, str], float]:
    """Reference implementation: enumerate every assignment and return the
    decisions + log-EV that maximizes the joint hit probability."""
    best_log = float("-inf")
    best_decisions: dict[str, str] = {}
    for combo in itertools.product(["fixed", "double", "triple"], repeat=len(options)):
        d = combo.count("double")
        t = combo.count("triple")
        if d > max_doubles or t > max_triples:
            continue
        log_total = 0.0
        for opt, kind in zip(options, combo, strict=True):
            if kind == "fixed":
                log_total += math.log(max(opt.top1, 1e-6))
            elif kind == "double":
                log_total += math.log(max(opt.top1 + opt.top2, 1e-6))
            # triple contributes log(1) == 0.
        if log_total > best_log:
            best_log = log_total
            best_decisions = {opt.match_id: kind for opt, kind in zip(options, combo, strict=True)}
    return best_decisions, best_log


def test_optimize_with_no_budget_returns_all_fixed() -> None:
    options = [_option("a", 0.5, 0.3), _option("b", 0.4, 0.3)]
    plan = optimize_ticket(options, max_doubles=0, max_triples=0)
    assert plan.decisions == {"a": "fixed", "b": "fixed"}
    assert math.isclose(plan.expected_correct, 0.5 * 0.4, abs_tol=1e-6)


def test_doubles_go_to_the_largest_second_outcome_gain() -> None:
    """With one double slot, the optimizer must pick the match where adding
    the second outcome yields the largest relative bump in hit probability."""
    # Match A: 0.50 / 0.10  -> double gain factor = 1.2 (small)
    # Match B: 0.40 / 0.35  -> double gain factor = 1.875 (large)
    options = [_option("A", 0.50, 0.10), _option("B", 0.40, 0.35)]
    plan = optimize_ticket(options, max_doubles=1, max_triples=0)
    assert plan.decisions == {"A": "fixed", "B": "double"}


def test_triple_lands_on_the_noisy_match_not_the_safe_one() -> None:
    """A truly noisy match (p1~0.34) absorbs the triple budget; the safe
    match (p1=0.85) is never wasted on a triple because its marginal gain
    is much smaller than the noisy match's."""
    options = [
        _option("safe", 0.85, 0.10),
        _option("noisy", 0.34, 0.33, 0.33),
    ]
    plan = optimize_ticket(options, max_doubles=0, max_triples=1)
    assert plan.decisions["noisy"] == "triple"
    assert plan.decisions["safe"] == "fixed"


def test_optimizer_matches_brute_force_on_small_slates() -> None:
    """Random-ish small slates: the DP must match a brute-force enumeration."""
    options = [
        _option("m1", 0.55, 0.25),
        _option("m2", 0.40, 0.35),
        _option("m3", 0.60, 0.22),
        _option("m4", 0.33, 0.34, 0.33),
        _option("m5", 0.48, 0.30),
    ]
    for max_d, max_t in [(0, 0), (1, 0), (2, 0), (2, 1), (3, 2), (5, 2)]:
        plan = optimize_ticket(options, max_doubles=max_d, max_triples=max_t)
        expected_decisions, expected_log = _brute_force_best(options, max_d, max_t)
        assert math.isclose(plan.log_expected_correct, expected_log, abs_tol=1e-9), (
            f"max_d={max_d} max_t={max_t} dp={plan.log_expected_correct} brute={expected_log}"
        )
        # Decisions may differ when there are ties; the EV check above is the
        # authoritative invariant. We still verify the budget is respected.
        doubles, triples = coverage_split(plan)
        assert len(doubles) <= max_d
        assert len(triples) <= max_t


def test_optimizer_handles_zero_probabilities_gracefully() -> None:
    """A degenerate fixture with top1 == 0 must not produce -inf/NaN; the
    log floor keeps the optimizer numerically stable."""
    options = [_option("ghost", 0.0, 0.0, 1.0)]
    plan = optimize_ticket(options, max_doubles=0, max_triples=1)
    assert plan.decisions["ghost"] == "triple"
    assert math.isclose(plan.expected_correct, 1.0, abs_tol=1e-6)


def test_optimizer_returns_empty_plan_for_no_matches() -> None:
    plan = optimize_ticket([], max_doubles=5, max_triples=3)
    assert plan.decisions == {}
    assert plan.expected_correct == 1.0


def test_budget_is_spent_on_highest_marginal_gain_matches_first() -> None:
    """When every match has a clear favorite, the optimizer still spends
    its budget because adding outcomes monotonically improves P(hit). The
    important invariant is that the slots land on the *least confident*
    matches (largest marginal log-gain), not the safest ones."""
    options = [
        _option("safest", 0.99, 0.005),
        _option("safe", 0.95, 0.03),
        _option("decent", 0.80, 0.12),
        _option("noisier", 0.55, 0.25),
        _option("noisiest", 0.40, 0.35),
    ]
    plan = optimize_ticket(options, max_doubles=2, max_triples=0)
    # Both doubles must land on the two least confident matches, never on
    # the safest two.
    doubles, _ = coverage_split(plan)
    assert doubles == {"noisiest", "noisier"}, plan.decisions


@pytest.mark.parametrize("n_matches", [9, 14])
def test_optimizer_runs_in_constant_time_for_progol_slate_sizes(n_matches: int) -> None:
    """A 14-match slate with the heaviest realistic budgets must still run
    instantly. The number of DP states is `(n+1) * (K+1) * (M+1)` and the
    transition cost is constant, so a couple of milliseconds is plenty."""
    import time

    rng_seed = [(i % 7) / 10.0 for i in range(n_matches)]
    options = [_option(f"m{i}", 0.40 + s, 0.25, 0.35 - s) for i, s in enumerate(rng_seed)]
    started = time.perf_counter()
    plan = optimize_ticket(options, max_doubles=8, max_triples=4)
    elapsed = time.perf_counter() - started
    assert plan.decisions, "must return a plan"
    assert elapsed < 0.1, f"slate-size optimizer should be near-instant, took {elapsed:.3f}s"
