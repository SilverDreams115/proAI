"""Economic shadow checks for completed-slate learning reports."""
from __future__ import annotations

from app.services.learning_economics_service import build_economic_shadow
from app.services.learning_slate_scoring_service import (
    LearningSlateScoringService,
    score_comparable_slates,
)
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def test_economic_shadow_reports_break_even_without_roi_claim() -> None:
    rows = [
        {
            "position": 1,
            "actual": "L",
            "prediction": "L",
            "decision_probabilities": {"L": 0.6, "E": 0.25, "V": 0.15},
        },
        {
            "position": 2,
            "actual": "V",
            "prediction": "L",
            "decision_probabilities": {"L": 0.55, "E": 0.25, "V": 0.2},
        },
    ]
    report = build_economic_shadow(rows)
    assert report["payout_configured"] is False
    assert report["strategies"]["model_top1"]["perfect_covered"] is False
    assert report["strategies"]["model_top1"]["simulated_roi"] is None
    assert report["strategies"]["model_top2"]["combinations"] == 4


def test_economic_shadow_computes_roi_only_with_configured_payout() -> None:
    rows = [
        {
            "position": 1,
            "actual": "L",
            "prediction": "L",
            "decision_probabilities": {"L": 0.6, "E": 0.25, "V": 0.15},
        }
    ]
    report = build_economic_shadow(rows, unit_cost=1.0, payout_units=5.0)
    top1 = report["strategies"]["model_top1"]
    assert top1["perfect_covered"] is True
    assert top1["break_even_payout_units"] == 1.0
    assert top1["simulated_net_units"] == 4.0
    assert top1["simulated_roi"] == 4.0


def test_scoring_includes_economic_shadow(learn_db):  # noqa: F811
    slate = seed_official_slate(learn_db, draw="PG-ECON", n=4)
    report = LearningSlateScoringService(learn_db).score_slate(slate)
    assert report["economic_shadow"]["mode"] == "economic_shadow"
    assert report["economic_shadow"]["strategies"]["model_top1"]["positions"] == 4
    assert report["ticket_strategy_backtest"]["mode"] == "ticket_strategy_backtest"


def test_all_comparable_scores_include_economic_summary(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-ECON-SUM", n=4)
    report = score_comparable_slates(learn_db)
    summary = report["economic_shadow_summary"]["strategies"]["model_top1"]
    assert summary["slate_count"] == 1
    assert summary["complete_count"] == 1
    assert summary["total_cost_units"] == 1.0
    assert report["ticket_strategy_backtest_summary"]["best_strategy"] is not None
