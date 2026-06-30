"""Presentation confidence degradation — no "high" label on capped/flagged picks.

The model's confidence_band drives internal logic and is left intact; the
UI-facing presentation_confidence_band must never advertise "high" when the
pick was capped, flagged REVISAR/BLOQUEADO/extreme-without-evidence, low
evidence, draw-calibrated, or not playable as a simple pick.
"""
from __future__ import annotations

from app.domain.presentation_guard import (
    derive_presentation_confidence,
    derive_presentation_guard,
)


def _pc(**kw):
    base = dict(
        confidence_band="high",
        final_status="LISTO",
        flags=[],
        simple_allowed=True,
        draw_calibration_applied=False,
    )
    base.update(kw)
    return derive_presentation_confidence(**base)


def test_blocked_status_is_blocked():
    band, _ = _pc(confidence_band="medium", final_status="BLOQUEADO")
    assert band == "blocked"


def test_high_with_cap_and_revisar_degrades_to_review():
    band, reason = _pc(
        final_status="REVISAR",
        flags=["EXTREME_PROBABILITY_CAPPED", "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"],
        simple_allowed=False,
    )
    assert band == "review"
    assert "revisión" in reason or "ajustada" in reason


def test_high_with_cap_only_degrades_to_review():
    band, _ = _pc(final_status="LISTO", flags=["EXTREME_PROBABILITY_CAPPED"], simple_allowed=False)
    assert band == "review"


def test_low_evidence_cannot_be_high():
    band, _ = _pc(flags=["LOW_EVIDENCE"], simple_allowed=False)
    assert band == "low"


def test_draw_calibrated_cannot_be_high():
    band, _ = _pc(draw_calibration_applied=True, simple_allowed=False)
    assert band == "low"


def test_high_band_not_simple_allowed_is_review():
    # No flags, but the pick is not a defensible simple → must not show "high".
    band, _ = _pc(final_status="LISTO", flags=[], simple_allowed=False)
    assert band == "review"


def test_clean_high_simple_stays_high():
    band, _ = _pc(final_status="LISTO", flags=[], simple_allowed=True)
    assert band == "high"


def test_clean_medium_passthrough():
    band, _ = _pc(confidence_band="medium", final_status="LISTO", flags=[], simple_allowed=True)
    assert band == "medium"


def test_unanchored_fallback_extreme_is_unreliable():
    band, _ = _pc(
        final_status="LISTO",  # not blocked/revisar to isolate this rule
        flags=["FALLBACK_USED", "EXTREME_PROBABILITY_WITHOUT_EVIDENCE", "LOW_EVIDENCE"],
        simple_allowed=False,
    )
    assert band == "unreliable"


def test_pg2338_pos4_shape_is_not_high():
    # Japan-Sweden: raw 0.96 capped, REVISAR, extreme-without-evidence, fallback.
    g = derive_presentation_guard(
        recommended_outcome="1",
        final_status="REVISAR",
        risk_level="high",
        ticket_strategy="NO_DEJAR_SIMPLE",
        flags=[
            "INTERNATIONAL_FRIENDLY",
            "FALLBACK_USED",
            "SUSPICIOUS_CLASS_PROBABILITY",
            "EXTREME_PROBABILITY_WITHOUT_EVIDENCE",
            "EXTREME_PROBABILITY_CAPPED",
        ],
        fallback_used=True,
        visible_confidence="baja",
        confidence_band="high",  # model band still says high
        draw_calibration_applied=True,
    )
    assert g.simple_allowed is False
    assert g.presentation_confidence_band != "high"
    assert g.presentation_confidence_band in {"review", "unreliable", "blocked"}
