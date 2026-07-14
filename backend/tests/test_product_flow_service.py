from types import SimpleNamespace

from app.services.product_flow_service import _active_slate_contract
from app.services.product_flow_service import _betting_policy
from app.services.product_flow_service import _data_quality
from app.services.product_flow_service import _recommendation


def _money_mode_report(**overrides):
    base = {
        "slate": {"match_count": 9},
        "validation": {
            "prediction_status": "persisted",
            "data_blockers": [],
            "warnings": [],
        },
        "decision": {
            "status": "JUGAR_BALANCEADO",
            "confidence": "cautious",
            "recommended_ticket": "balanced",
            "reason": "Boleto balanceado cubre los riesgos principales.",
        },
        "tickets": {
            "balanced": {
                "estimated_combinations": 48,
                "estimated_cost": None,
                "cost_note": "precio no configurado",
            }
        },
        "do_not_simple_positions": [2, 5],
        "must_review_positions": [],
        "matches": [],
    }
    base.update(overrides)
    return base


def test_data_quality_score_uses_blockers_and_no_simple_positions() -> None:
    quality = _data_quality(
        _money_mode_report(
            validation={
                "prediction_status": "missing",
                "data_blockers": ["missing_predictions"],
                "warnings": ["low_provider_coverage"],
            },
            do_not_simple_positions=[1, 2, 3, 4, 5],
            must_review_positions=[1, 3],
        )
    )

    assert quality["level"] == "blocked"
    assert quality["score"] < 50
    assert "missing_predictions" in quality["blockers"]


def test_recommendation_keeps_final_decision_separate_from_explanation() -> None:
    recommendation = _recommendation(
        _money_mode_report(
            decision={
                "status": "NO_JUGAR",
                "confidence": "low",
                "recommended_ticket": None,
                "reason": "Datos incompletos graves.",
            }
        )
    )

    assert recommendation["internal_score"]["money_mode_status"] == "NO_JUGAR"
    assert recommendation["final_recommendation"] == "NO JUGAR"
    assert recommendation["explanation"]["why_not_play"] == "Datos incompletos graves."


def test_betting_policy_blocks_when_quality_is_blocked() -> None:
    policy = _betting_policy(
        _money_mode_report(decision={"status": "JUGAR_BALANCEADO", "recommended_ticket": "balanced"}),
        {"level": "blocked"},
    )

    assert policy["hard_no_play"] is True
    assert policy["action"] == "do_not_play"
    assert any("Nunca jugar" in limit for limit in policy["limits"])


def test_active_slate_contract_flags_archived_or_empty_visible_slates() -> None:
    selected = SimpleNamespace(id="s1", draw_code="PG-1", week_type="weekend", matches=[object()], is_archived=False)
    archived = SimpleNamespace(id="s2", draw_code="PG-2", week_type="midweek", matches=[], is_archived=True)

    contract = _active_slate_contract(selected, [selected, archived])

    assert contract["strict"] is False
    assert contract["by_week_type"] == {"weekend": 1, "midweek": 1}
    assert "PG-2: archived_visible" in contract["violations"]
    assert "PG-2: no_matches" in contract["violations"]
