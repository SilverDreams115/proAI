"""Read-only ticket strategy backtests for completed slates.

The scorer tells us whether the model picked correctly. This layer asks the
money question: which practical boleto rule would have covered the completed
slate, at what combination cost, and what payout would be needed to break even.
It is pure and deterministic so future slates can be compared against the same
strategy catalog.
"""
from __future__ import annotations

from typing import Any

SIGNS = ("L", "E", "V")


def build_ticket_strategy_backtest(
    rows: list[dict[str, Any]],
    *,
    unit_cost: float = 1.0,
    payout_units: float | None = None,
) -> dict[str, Any]:
    strategies = [_evaluate_strategy(rows, spec, unit_cost, payout_units) for spec in _strategy_specs(len(rows))]
    ranked = sorted(strategies, key=_rank_key)
    best = ranked[0] if ranked else None
    return {
        "mode": "ticket_strategy_backtest",
        "strategy_count": len(strategies),
        "best_strategy": _summary(best) if best else None,
        "strategies": strategies,
        "ranking_policy": (
            "prefer perfect coverage, then higher covered positions, then lower cost; "
            "ROI is only used when payout is configured"
        ),
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def summarize_ticket_strategy_backtests(slates: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, Any]] = {}
    for slate in slates:
        report = slate.get("ticket_strategy_backtest") or {}
        for strategy in report.get("strategies") or []:
            key = strategy["key"]
            summary = by_strategy.setdefault(
                key,
                {
                    "strategy": strategy["strategy"],
                    "slate_count": 0,
                    "complete_count": 0,
                    "perfect_count": 0,
                    "covered_positions": 0,
                    "positions": 0,
                    "total_cost_units": 0.0,
                    "total_net_units": None,
                },
            )
            summary["slate_count"] += 1
            if strategy.get("data_quality") == "complete":
                summary["complete_count"] += 1
            if strategy.get("perfect_covered"):
                summary["perfect_count"] += 1
            summary["covered_positions"] += int(strategy.get("covered_positions") or 0)
            summary["positions"] += int(strategy.get("positions") or 0)
            summary["total_cost_units"] = round(
                summary["total_cost_units"] + float(strategy.get("cost_units") or 0.0),
                4,
            )
            if strategy.get("simulated_net_units") is not None:
                current = 0.0 if summary["total_net_units"] is None else summary["total_net_units"]
                summary["total_net_units"] = round(current + float(strategy["simulated_net_units"]), 4)

    for summary in by_strategy.values():
        positions = int(summary["positions"] or 0)
        cost = float(summary["total_cost_units"] or 0.0)
        net = summary["total_net_units"]
        summary["coverage_rate"] = (
            round(float(summary["covered_positions"]) / positions, 4) if positions else None
        )
        summary["simulated_roi"] = round(float(net) / cost, 4) if net is not None and cost else None

    ranked = sorted(by_strategy.values(), key=_summary_rank_key)
    return {
        "mode": "ticket_strategy_backtest_summary",
        "best_strategy": ranked[0] if ranked else None,
        "strategies": by_strategy,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def _strategy_specs(match_count: int) -> list[dict[str, Any]]:
    budget_32_doubles = _max_doubles_for_budget(32)
    budget_128_doubles = _max_doubles_for_budget(128)
    return [
        {"key": "top1_only", "strategy": "Top 1 puro", "kind": "top1", "max_doubles": 0},
        {"key": "top2_all", "strategy": "Top 2 todos", "kind": "top2_all", "max_doubles": match_count},
        {
            "key": "uncertainty_4_doubles",
            "strategy": "4 dobles por incertidumbre",
            "kind": "uncertainty",
            "max_doubles": min(4, match_count),
        },
        {
            "key": "uncertainty_6_doubles",
            "strategy": "6 dobles por incertidumbre",
            "kind": "uncertainty",
            "max_doubles": min(6, match_count),
        },
        {
            "key": "guardrail_first_6_doubles",
            "strategy": "Guardrail primero + 6 dobles",
            "kind": "guardrail_first",
            "max_doubles": min(6, match_count),
        },
        {
            "key": "budget_32",
            "strategy": "Presupuesto <=32 combinaciones",
            "kind": "uncertainty",
            "max_doubles": min(budget_32_doubles, match_count),
        },
        {
            "key": "budget_128",
            "strategy": "Presupuesto <=128 combinaciones",
            "kind": "uncertainty",
            "max_doubles": min(budget_128_doubles, match_count),
        },
    ]


def _evaluate_strategy(
    rows: list[dict[str, Any]],
    spec: dict[str, Any],
    unit_cost: float,
    payout_units: float | None,
) -> dict[str, Any]:
    selected = _selected_double_positions(rows, spec)
    selections: list[dict[str, Any]] = []
    combinations = 1
    covered = 0
    scorable = 0
    missing_actual = 0
    missing_probabilities = 0

    for row in sorted(rows, key=lambda item: int(item.get("position") or 0)):
        actual = row.get("actual")
        ranked = _ranked(row)
        if actual is None:
            missing_actual += 1
        if not ranked:
            missing_probabilities += 1
        picks = _picks_for_row(row, ranked, selected)
        if actual is not None and picks:
            scorable += 1
            if actual in picks:
                covered += 1
        combinations *= max(len(picks), 1)
        selections.append(
            {
                "position": row.get("position"),
                "actual": actual,
                "picks": picks,
                "type": "double" if len(picks) == 2 else "simple",
                "covered": actual in picks if actual is not None and picks else None,
                "margin": _margin(row),
                "guardrail_status": row.get("guardrail_status"),
            }
        )

    complete = bool(rows) and missing_actual == 0 and missing_probabilities == 0
    perfect = complete and covered == len(rows)
    cost = round(combinations * unit_cost, 4)
    net = None
    roi = None
    if payout_units is not None:
        payout = payout_units if perfect else 0.0
        net = round(payout - cost, 4)
        roi = round(net / cost, 4) if cost else None

    return {
        "key": spec["key"],
        "strategy": spec["strategy"],
        "positions": len(rows),
        "scorable_positions": scorable,
        "covered_positions": covered,
        "coverage_rate": round(covered / scorable, 4) if scorable else None,
        "perfect_covered": perfect,
        "simple_count": sum(1 for item in selections if item["type"] == "simple"),
        "double_count": sum(1 for item in selections if item["type"] == "double"),
        "triple_count": 0,
        "combinations": combinations,
        "cost_units": cost,
        "break_even_payout_units": cost if perfect else None,
        "simulated_net_units": net,
        "simulated_roi": roi,
        "data_quality": "complete" if complete else "incomplete",
        "missing_actual_count": missing_actual,
        "missing_probability_count": missing_probabilities,
        "selections": selections,
    }


def _selected_double_positions(rows: list[dict[str, Any]], spec: dict[str, Any]) -> set[int]:
    max_doubles = int(spec.get("max_doubles") or 0)
    if max_doubles <= 0:
        return set()
    if spec["kind"] == "top2_all":
        return {int(row["position"]) for row in rows if row.get("position") is not None}
    if spec["kind"] == "guardrail_first":
        guardrail = [row for row in rows if row.get("guardrail_status") in {"no_simple", "blocked"}]
        rest = [row for row in rows if row not in guardrail]
        ordered = sorted(guardrail, key=_margin) + sorted(rest, key=_margin)
    else:
        ordered = sorted(rows, key=_margin)
    return {
        int(row["position"])
        for row in ordered[:max_doubles]
        if row.get("position") is not None and len(_ranked(row)) >= 2
    }


def _picks_for_row(row: dict[str, Any], ranked: list[str], selected: set[int]) -> list[str]:
    if not ranked:
        prediction = row.get("prediction")
        return [prediction] if prediction in SIGNS else []
    position = int(row.get("position") or 0)
    return ranked[:2] if position in selected else ranked[:1]


def _ranked(row: dict[str, Any]) -> list[str]:
    probs = row.get("decision_probabilities") or {}
    return [
        sign for sign in sorted(SIGNS, key=lambda item: float(probs.get(item) or 0.0), reverse=True)
        if sign in probs
    ]


def _margin(row: dict[str, Any]) -> float:
    probs = row.get("decision_probabilities") or {}
    ranked_values = sorted((float(probs.get(sign) or 0.0) for sign in SIGNS), reverse=True)
    if len(ranked_values) < 2:
        return 1.0
    return round(ranked_values[0] - ranked_values[1], 6)


def _max_doubles_for_budget(max_combinations: int) -> int:
    doubles = 0
    combinations = 1
    while combinations * 2 <= max_combinations:
        combinations *= 2
        doubles += 1
    return doubles


def _rank_key(strategy: dict[str, Any]) -> tuple[int, int, float, float]:
    cost = float(strategy.get("cost_units") or 0.0)
    roi = strategy.get("simulated_roi")
    roi_score = float(roi) if roi is not None else 0.0
    return (
        0 if strategy.get("perfect_covered") else 1,
        -int(strategy.get("covered_positions") or 0),
        -roi_score,
        cost,
    )


def _summary_rank_key(summary: dict[str, Any]) -> tuple[int, int, float, float]:
    cost = float(summary.get("total_cost_units") or 0.0)
    roi = summary.get("simulated_roi")
    roi_score = float(roi) if roi is not None else 0.0
    return (
        -int(summary.get("perfect_count") or 0),
        -int(summary.get("covered_positions") or 0),
        -roi_score,
        cost,
    )


def _summary(strategy: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "key",
        "strategy",
        "covered_positions",
        "positions",
        "coverage_rate",
        "perfect_covered",
        "double_count",
        "combinations",
        "cost_units",
        "break_even_payout_units",
        "simulated_roi",
    )
    return {key: strategy.get(key) for key in keys}
