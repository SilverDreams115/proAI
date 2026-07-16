"""Read-only economic shadow metrics for completed Progol slates.

This module intentionally does not estimate prizes. It measures cost pressure,
perfect-coverage, and the payout required to break even. If an operator provides
an external payout assumption, it can also compute a simulated net/ROI, but the
default is conservative: no payout, no ROI claim.
"""
from __future__ import annotations

from typing import Any

SIGNS = ("L", "E", "V")


def build_economic_shadow(
    rows: list[dict[str, Any]],
    *,
    unit_cost: float = 1.0,
    payout_units: float | None = None,
) -> dict[str, Any]:
    """Build strategy-level economics from scored by-position rows.

    `rows` is the `by_position` payload from `LearningSlateScoringService`.
    The function is pure and read-only so the API, CLI and tests can share the
    same contract for every future slate.
    """
    strategies = {
        "model_top1": _strategy(rows, "top1", unit_cost=unit_cost, payout_units=payout_units),
        "model_top2": _strategy(rows, "top2", unit_cost=unit_cost, payout_units=payout_units),
        "full_cover": _strategy(rows, "full", unit_cost=unit_cost, payout_units=payout_units),
    }
    complete = all(item["data_quality"] == "complete" for item in strategies.values())
    return {
        "mode": "economic_shadow",
        "unit_cost": unit_cost,
        "payout_units": payout_units,
        "payout_configured": payout_units is not None,
        "complete": complete,
        "strategies": strategies,
        "note": (
            "ROI is simulated from configured payout_units and is not a profit guarantee."
            if payout_units is not None
            else "No payout configured; report shows cost pressure and break-even only."
        ),
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def summarize_economic_shadow(slates: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, Any]] = {}
    for slate in slates:
        shadow = slate.get("economic_shadow") or {}
        for key, item in (shadow.get("strategies") or {}).items():
            summary = by_strategy.setdefault(
                key,
                {
                    "slate_count": 0,
                    "complete_count": 0,
                    "perfect_count": 0,
                    "total_cost_units": 0.0,
                    "total_net_units": None,
                },
            )
            summary["slate_count"] += 1
            if item.get("data_quality") == "complete":
                summary["complete_count"] += 1
            if item.get("perfect_covered"):
                summary["perfect_count"] += 1
            summary["total_cost_units"] = round(
                summary["total_cost_units"] + float(item.get("cost_units") or 0.0),
                4,
            )
            if item.get("simulated_net_units") is not None:
                current = 0.0 if summary["total_net_units"] is None else summary["total_net_units"]
                summary["total_net_units"] = round(current + float(item["simulated_net_units"]), 4)
    for summary in by_strategy.values():
        cost = float(summary["total_cost_units"] or 0.0)
        net = summary["total_net_units"]
        summary["simulated_roi"] = round(float(net) / cost, 4) if net is not None and cost else None
    return {
        "mode": "economic_shadow_summary",
        "strategies": by_strategy,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def _strategy(
    rows: list[dict[str, Any]],
    mode: str,
    *,
    unit_cost: float,
    payout_units: float | None,
) -> dict[str, Any]:
    selections: list[dict[str, Any]] = []
    combinations = 1
    covered = 0
    scorable = 0
    missing_actual = 0
    missing_probabilities = 0

    for row in rows:
        actual = row.get("actual")
        probs = row.get("decision_probabilities") or {}
        picks = _picks(probs, mode)
        if actual is None:
            missing_actual += 1
        if not picks:
            missing_probabilities += 1
            picks = [row["prediction"]] if row.get("prediction") in SIGNS else []
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
                "covered": actual in picks if actual is not None and picks else None,
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
        "mode": mode,
        "positions": len(rows),
        "scorable_positions": scorable,
        "covered_positions": covered,
        "perfect_covered": perfect,
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


def _picks(probs: dict[str, Any], mode: str) -> list[str]:
    ranked = [
        sign for sign in sorted(SIGNS, key=lambda s: float(probs.get(s) or 0.0), reverse=True)
        if sign in probs
    ]
    if mode == "top1":
        return ranked[:1]
    if mode == "top2":
        return ranked[:2]
    return list(SIGNS) if ranked else []
