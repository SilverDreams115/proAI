"""R7.0 — learning error attribution (read-only)."""
from __future__ import annotations

from app.services.learning_error_attribution_service import (
    build_error_attribution,
    classify_position,
)
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def test_classify_wrong_favorite():
    """10 — an unguarded miss where no class dominated is a wrong_favorite."""
    out = classify_position(
        prediction_sign="L",
        actual_sign="V",
        decision_probs={"L": 0.4, "E": 0.25, "V": 0.35},
        final_status="LISTO",
        money_blocked=False,
    )
    assert out["error_type"] == "wrong_favorite"
    assert out["should_have_blocked"] is True


def test_classify_favorite_overestimated():
    out = classify_position(
        prediction_sign="L",
        actual_sign="V",
        decision_probs={"L": 0.7, "E": 0.2, "V": 0.1},
        final_status="LISTO",
        money_blocked=False,
    )
    assert out["error_type"] == "favorite_overestimated"


def test_classify_guardrail_saved():
    """11a — a miss the guardrail had degraded (REVISAR/BLOQUEADO) is guardrail_saved."""
    out = classify_position(
        prediction_sign="L",
        actual_sign="V",
        decision_probs={"L": 0.5, "E": 0.25, "V": 0.25},
        final_status="BLOQUEADO",
        money_blocked=False,
    )
    assert out["error_type"] == "guardrail_saved"
    # The guardrail already degraded this pick, so the system did NOT fail to
    # block it — should_have_blocked is only true for unguarded losses.
    assert out["should_have_blocked"] is False


def test_money_mode_correctly_blocked_when_blocking_a_loss():
    """11b — Money Mode blocking a losing pick is tracked as a correct block,
    without masking the underlying signal error."""
    out = classify_position(
        prediction_sign="L",
        actual_sign="V",
        decision_probs={"L": 0.4, "E": 0.25, "V": 0.35},
        final_status="LISTO",
        money_blocked=True,
    )
    assert out["money_mode_label"] == "money_mode_correctly_blocked"
    assert out["money_mode_decision_correct"] is True
    assert out["error_type"] == "wrong_favorite"  # signal error preserved


def test_attribution_summary_on_slate(learn_db):  # noqa: F811
    slate = seed_official_slate(learn_db, draw="PG-ATTR", n=4)
    report = build_error_attribution(learn_db, slate)
    assert report["comparable"] is True
    assert "by_error_type" in report["summary"]
    assert sum(report["summary"]["by_error_type"].values()) == 4
