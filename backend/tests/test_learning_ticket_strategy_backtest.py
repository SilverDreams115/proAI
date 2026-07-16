"""Ticket strategy backtest checks for completed-slate learning reports."""
from __future__ import annotations

from app.services.learning_slate_scoring_service import (
    LearningSlateScoringService,
    score_comparable_slates,
)
from app.services.learning_ticket_strategy_backtest_service import build_ticket_strategy_backtest
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def _rows() -> list[dict]:
    return [
        {
            "position": 1,
            "actual": "L",
            "prediction": "L",
            "decision_probabilities": {"L": 0.45, "E": 0.4, "V": 0.15},
            "guardrail_status": "ready",
        },
        {
            "position": 2,
            "actual": "V",
            "prediction": "L",
            "decision_probabilities": {"L": 0.42, "V": 0.4, "E": 0.18},
            "guardrail_status": "no_simple",
        },
        {
            "position": 3,
            "actual": "E",
            "prediction": "L",
            "decision_probabilities": {"L": 0.7, "E": 0.2, "V": 0.1},
            "guardrail_status": "ready",
        },
        {
            "position": 4,
            "actual": "L",
            "prediction": "L",
            "decision_probabilities": {"L": 0.72, "E": 0.18, "V": 0.1},
            "guardrail_status": "ready",
        },
        {
            "position": 5,
            "actual": "L",
            "prediction": "L",
            "decision_probabilities": {"L": 0.74, "E": 0.16, "V": 0.1},
            "guardrail_status": "ready",
        },
    ]


def test_ticket_strategy_backtest_prefers_practical_perfect_strategy() -> None:
    report = build_ticket_strategy_backtest(_rows())
    by_key = {item["key"]: item for item in report["strategies"]}
    assert by_key["top1_only"]["perfect_covered"] is False
    assert by_key["uncertainty_4_doubles"]["perfect_covered"] is True
    assert by_key["uncertainty_4_doubles"]["combinations"] == 16
    assert by_key["top2_all"]["combinations"] == 32
    assert report["best_strategy"]["key"] == "uncertainty_4_doubles"


def test_ticket_strategy_backtest_respects_budget_caps() -> None:
    report = build_ticket_strategy_backtest(_rows())
    by_key = {item["key"]: item for item in report["strategies"]}
    assert by_key["budget_32"]["combinations"] <= 32
    assert by_key["budget_128"]["combinations"] <= 128


def test_scoring_includes_ticket_strategy_backtest(learn_db):  # noqa: F811
    slate = seed_official_slate(learn_db, draw="PG-TICKET-BT", n=4)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["ticket_strategy_backtest"]["mode"] == "ticket_strategy_backtest"
    assert report["ticket_strategy_backtest"]["best_strategy"]["positions"] == 4


def test_all_comparable_scores_include_ticket_strategy_summary(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-TICKET-BT-SUM", n=4)
    report = score_comparable_slates(learn_db)
    summary = report["ticket_strategy_backtest_summary"]
    assert summary["mode"] == "ticket_strategy_backtest_summary"
    assert summary["best_strategy"]["slate_count"] == 1
