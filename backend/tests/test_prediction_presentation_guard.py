"""Presentation guard (R5.6-D) — risky predictions never read as simple picks."""
from __future__ import annotations

from app.domain.presentation_guard import derive_presentation_guard


def _norway_france_guard():
    # Exact field shape served for PG-2338 pos 7 Norway vs France.
    return derive_presentation_guard(
        recommended_outcome="2",
        final_status="REVISAR",
        risk_level="high",
        ticket_strategy="NO_DEJAR_SIMPLE",
        flags=[
            "LOW_EVIDENCE",
            "INTERNATIONAL_FRIENDLY",
            "FALLBACK_USED",
            "SUSPICIOUS_CLASS_PROBABILITY",
            "EXTREME_PROBABILITY_CAPPED",
            "FRIENDLY_UNCERTAINTY_PENALTY",
        ],
        fallback_used=True,
        visible_confidence="media-baja",
    )


def test_norway_france_is_not_simple_but_keeps_primary_signal():
    g = _norway_france_guard()
    assert g.simple_allowed is False
    assert g.primary_signal == "V"  # señal principal preserved
    assert g.recommendation_label == "NO SIMPLE"
    assert g.risk_level == "high"
    assert "risk_high" in g.reason
    assert "no_dejar_simple" in g.reason
    assert "suspicious_class" in g.reason
    assert "fallback_low_evidence" in g.reason


def test_high_risk_alone_blocks_simple():
    g = derive_presentation_guard(
        recommended_outcome="1",
        final_status="LISTO",
        risk_level="high",
        ticket_strategy="DOBLE_RECOMENDADO",
        flags=[],
        fallback_used=False,
        visible_confidence="media",
    )
    assert g.simple_allowed is False
    assert g.recommendation_label == "NO SIMPLE"
    assert g.primary_signal == "L"
    assert "risk_high" in g.reason


def test_no_dejar_simple_strategy_blocks_simple():
    g = derive_presentation_guard(
        recommended_outcome="X",
        final_status="REVISAR",
        risk_level="medium",
        ticket_strategy="NO_DEJAR_SIMPLE",
        flags=[],
        fallback_used=False,
        visible_confidence="media-baja",
    )
    assert g.simple_allowed is False
    assert "no_dejar_simple" in g.reason
    assert g.primary_signal == "E"


def test_blocked_status_label():
    g = derive_presentation_guard(
        recommended_outcome="1",
        final_status="BLOQUEADO",
        risk_level="high",
        ticket_strategy="EVITAR",
        flags=["BLOCKED_INSUFFICIENT_DATA"],
        fallback_used=False,
        visible_confidence="baja",
    )
    assert g.simple_allowed is False
    assert g.recommendation_label == "BLOQUEADO"


def test_clean_simple_pick_is_allowed():
    """A FIJO, low-risk, SIMPLE-strategy match is the only case that plays simple."""
    g = derive_presentation_guard(
        recommended_outcome="1",
        final_status="FIJO",
        risk_level="low",
        ticket_strategy="SIMPLE",
        flags=[],
        fallback_used=False,
        visible_confidence="alta",
    )
    assert g.simple_allowed is True
    assert g.recommendation_label == "SIMPLE"
    assert g.reason == []
    assert g.primary_signal == "L"
