"""Tests for the coverage-target optimizer (Fase 5.2)."""
from __future__ import annotations

import math

from app.services.coverage import prob_at_least
from app.services.ticket_optimizer import (
    TicketOption,
    coverage_split,
    min_budget_for_target,
    optimize_for_coverage,
)


def _option(match_id: str, top1: float, top2: float) -> TicketOption:
    return TicketOption(match_id=match_id, top1=top1, top2=top2, top3=max(0.0, 1.0 - top1 - top2))


def test_zero_budget_returns_all_fixed_with_correct_probability() -> None:
    options = [_option("a", 0.55, 0.25), _option("b", 0.40, 0.30)]
    plan = optimize_for_coverage(options, max_doubles=0, max_triples=0, min_correct=2)
    assert plan.decisions == {"a": "fixed", "b": "fixed"}
    # P(both right) when fixed = 0.55 * 0.40 = 0.22
    assert math.isclose(plan.probability_target_met, 0.22, abs_tol=1e-6)


def test_triple_floods_one_match_to_certainty() -> None:
    """With one triple available, the optimizer must use it on the
    match where it raises P(>=K) most."""
    options = [_option("certain", 0.85, 0.10), _option("uncertain", 0.40, 0.30)]
    plan = optimize_for_coverage(options, max_doubles=0, max_triples=1, min_correct=2)
    # Tripling 'uncertain' raises P(both) from 0.85*0.40=0.34 to 0.85*1.0=0.85.
    # Tripling 'certain' raises it from 0.34 to 1.0*0.40=0.40.
    # The first is much better.
    assert plan.decisions["uncertain"] == "triple"
    assert plan.decisions["certain"] == "fixed"
    assert math.isclose(plan.probability_target_met, 0.85, abs_tol=1e-6)


def test_optimizer_maximizes_tail_not_log_ev() -> None:
    """`optimize_ticket` maximizes log-EV (perfect ticket), this one
    maximizes P(>=K). They can disagree when the threshold is below N:
    if going for >= N-1 of N, the optimizer prefers to lock in the
    weakest match with a triple, not the marginally-best double."""
    options = [
        _option("strong", 0.62, 0.20),
        _option("medium", 0.45, 0.30),
        _option("weak", 0.34, 0.33),
    ]
    plan = optimize_for_coverage(options, max_doubles=0, max_triples=1, min_correct=2)
    # Min correct = 2 of 3. Spending the triple on the weakest match
    # gives the highest P(>=2). Compare to the alternatives manually:
    # triple-weak: p = [0.62, 0.45, 1.0] -> P(>=2) computed below.
    # triple-medium: p = [0.62, 1.0, 0.34] -> P(>=2).
    # triple-strong: p = [1.0, 0.45, 0.34] -> P(>=2).
    options_probs = {
        "triple_weak": [0.62, 0.45, 1.0],
        "triple_medium": [0.62, 1.0, 0.34],
        "triple_strong": [1.0, 0.45, 0.34],
    }
    tails = {k: prob_at_least(v, 2) for k, v in options_probs.items()}
    best_label = max(tails, key=lambda key: tails[key])
    if best_label == "triple_weak":
        assert plan.decisions["weak"] == "triple"
    elif best_label == "triple_medium":
        assert plan.decisions["medium"] == "triple"
    else:
        assert plan.decisions["strong"] == "triple"


def test_optimizer_meets_target_when_budget_is_generous() -> None:
    """With enough budget, P(>=K) reaches 1.0 — the optimizer must
    return the smallest assignment that achieves the target. With 4
    matches and K=3, 3 triples are enough (the leftover match can stay
    fixed), so we check the achieved probability rather than the
    specific allocation."""
    options = [_option(f"m{i}", 0.40, 0.30) for i in range(4)]
    plan = optimize_for_coverage(options, max_doubles=0, max_triples=4, min_correct=3)
    triple_count = sum(1 for kind in plan.decisions.values() if kind == "triple")
    assert triple_count >= 3, f"need at least 3 triples to lock K=3, got {triple_count}"
    assert math.isclose(plan.probability_target_met, 1.0, abs_tol=1e-9)


def test_coverage_split_returns_double_and_triple_ids() -> None:
    options = [_option("safe", 0.60, 0.20), _option("noise", 0.34, 0.33)]
    plan = optimize_for_coverage(options, max_doubles=1, max_triples=1, min_correct=2)
    doubles, triples = coverage_split(plan)  # type: ignore[arg-type]
    assert isinstance(doubles, set) and isinstance(triples, set)


def test_min_budget_reaches_target_when_feasible() -> None:
    """Walk up budgets until 90% confidence is met."""
    options = [_option(f"m{i}", 0.45, 0.30) for i in range(5)]
    report = min_budget_for_target(
        options, target_probability=0.90, min_correct=4, max_doubles_cap=5, max_triples_cap=5
    )
    assert report.target_reached, f"expected to reach 0.90, got {report.plan.probability_target_met}"
    assert report.plan.probability_target_met >= 0.90


def test_min_budget_reports_best_effort_when_target_unreachable() -> None:
    """Demanding 4/4 when no triples are allowed and individual p~0.45
    is infeasible; the report must say `target_reached=False` and still
    return the best plan found."""
    options = [_option(f"m{i}", 0.45, 0.30) for i in range(4)]
    report = min_budget_for_target(
        options, target_probability=0.99, min_correct=4, max_doubles_cap=4, max_triples_cap=0
    )
    assert not report.target_reached
    # Best plan must still be a valid coverage assignment.
    assert len(report.plan.decisions) == 4


def test_progol_media_semana_scale_runs_under_a_second() -> None:
    """9-match slate with realistic budget must finish quickly."""
    import time

    options = [_option(f"m{i}", 0.42 + (i % 4) * 0.05, 0.28) for i in range(9)]
    started = time.perf_counter()
    plan = optimize_for_coverage(options, max_doubles=6, max_triples=4, min_correct=8)
    elapsed = time.perf_counter() - started
    assert plan.decisions
    assert elapsed < 1.0, f"PGM-scale coverage optimizer too slow: {elapsed:.3f}s"
