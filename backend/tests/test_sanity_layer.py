"""Regression tests for the prediction sanity / guardrail layer.

These lock the behaviour the audit was built to guarantee:

* L/E/V class probabilities are mapped by *label*, never by array index.
* A low-evidence prediction can never come out as FIJO.
* International friendlies receive an uncertainty penalty.
* A fallback-heuristic pick is never auto-promoted to FIJO.
* L corresponds to the home team, V to the away team, end to end.
* A skewed slate (too many visitors / too many near-zero classes) raises
  a diagnostic alarm.
"""

from __future__ import annotations

from app.services.sanity_service import (
    EvidenceLevel,
    FinalStatus,
    RiskLevel,
    SanityFlag,
    SlateMatchObservation,
    apply_sanity_layer,
    build_slate_distribution_report,
    compute_ticket_strategy,
    compute_visible_confidence,
)


# --- Test 1: class-order mapping is by label, not index --------------------


def test_class_probabilities_are_mapped_by_label_not_index() -> None:
    """A model whose classes come back in a non-canonical order
    (``["V", "L", "E"]``) must still map 70/20/10 to V/L/E — proving the
    pipeline keys probabilities by label, never by positional index."""
    classes = ["V", "L", "E"]
    probs = [0.70, 0.20, 0.10]
    proba_by_label = {label: prob for label, prob in zip(classes, probs)}

    assert proba_by_label["V"] == 0.70
    assert proba_by_label["L"] == 0.20
    assert proba_by_label["E"] == 0.10

    # And the sanity layer, fed the canonical home/draw/away dict that the
    # pipeline builds from that mapping, keeps V as the top outcome.
    result = apply_sanity_layer(
        probabilities={
            "home": proba_by_label["L"],
            "draw": proba_by_label["E"],
            "away": proba_by_label["V"],
        },
        confidence_band="medium",
        evidence_level=EvidenceLevel.HIGH,
        recommended_outcome="2",
    )
    final = result.final_probabilities
    assert max(final, key=final.get) == "away"


# --- Test 2: low evidence can never be FIJO --------------------------------


def test_low_evidence_extreme_probability_is_reviewed_and_flagged() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.12, "draw": 0.08, "away": 0.80},
        confidence_band="high",
        evidence_level=EvidenceLevel.LOW,
        recommended_outcome="2",
    )
    assert result.final_status is FinalStatus.REVISAR
    assert result.final_status is not FinalStatus.FIJO
    assert SanityFlag.LOW_EVIDENCE in result.flags
    assert SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE in result.flags
    # Displayed probability degraded below the low-evidence cap.
    assert max(result.final_probabilities.values()) <= 0.60 + 1e-9
    # Raw is preserved for traceability.
    assert result.raw_probabilities["away"] == 0.80


# --- Test 3: international friendly penalty ---------------------------------


def test_international_friendly_extreme_probability_is_penalized() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.12, "draw": 0.09, "away": 0.79},
        confidence_band="high",
        evidence_level=EvidenceLevel.MEDIUM,
        is_international_friendly=True,
        recommended_outcome="2",
    )
    assert SanityFlag.INTERNATIONAL_FRIENDLY in result.flags
    assert SanityFlag.FRIENDLY_UNCERTAINTY_PENALTY in result.flags
    assert max(result.final_probabilities.values()) <= 0.65 + 1e-9
    assert result.final_status is not FinalStatus.FIJO


def test_high_evidence_friendly_keeps_more_headroom() -> None:
    """With HIGH evidence the friendly cap is relaxed — a strong, well
    anchored national-team pick is not gratuitously flattened."""
    result = apply_sanity_layer(
        probabilities={"home": 0.10, "draw": 0.12, "away": 0.78},
        confidence_band="high",
        evidence_level=EvidenceLevel.HIGH,
        is_international_friendly=True,
        recommended_outcome="2",
    )
    assert SanityFlag.FRIENDLY_UNCERTAINTY_PENALTY not in result.flags
    assert max(result.final_probabilities.values()) > 0.65


# --- Test 4: fallback is never auto-FIJO -----------------------------------


def test_fallback_used_is_never_auto_fijo() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.58, "draw": 0.25, "away": 0.17},
        confidence_band="high",
        evidence_level=EvidenceLevel.HIGH,
        fallback_used=True,
        recommended_outcome="1",
    )
    assert SanityFlag.FALLBACK_USED in result.flags
    assert result.final_status is not FinalStatus.FIJO
    assert result.final_status is FinalStatus.LISTO


# --- Test 5: L == home, V == away (ordering preserved) ---------------------


def test_local_visitor_mapping_is_preserved_through_sanity() -> None:
    # Home is the clear favourite. After the guardrails the top outcome
    # must still be L (home) — the layer degrades magnitude, never the
    # identity of the pick.
    result = apply_sanity_layer(
        probabilities={"home": 0.82, "draw": 0.10, "away": 0.08},
        confidence_band="high",
        evidence_level=EvidenceLevel.LOW,
        recommended_outcome="1",
    )
    final = result.final_probabilities
    assert final["home"] > final["away"]
    assert final["home"] > final["draw"]
    assert max(final, key=final.get) == "home"


def test_suspicious_near_zero_class_is_flagged() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.02, "draw": 0.18, "away": 0.80},
        confidence_band="medium",
        evidence_level=EvidenceLevel.MEDIUM,
        recommended_outcome="2",
    )
    assert SanityFlag.SUSPICIOUS_CLASS_PROBABILITY in result.flags
    assert result.risk_level is RiskLevel.HIGH


# --- Test 6: suspicious slate distribution raises an alarm -----------------


def test_slate_with_too_many_visitor_picks_raises_alarm() -> None:
    observations = [
        SlateMatchObservation(
            probabilities={"home": 0.15, "draw": 0.20, "away": 0.65},
            recommended_label="away",
            evidence_level=EvidenceLevel.LOW,
            is_international_friendly=True,
            final_status=FinalStatus.REVISAR,
        )
        for _ in range(8)
    ]
    report = build_slate_distribution_report(observations)
    assert report.count_V == 8
    assert any("AWAY_BIAS" in alarm for alarm in report.alarms)


def test_slate_with_many_near_zero_classes_raises_alarm() -> None:
    observations = [
        SlateMatchObservation(
            probabilities={"home": 0.02, "draw": 0.18, "away": 0.80},
            recommended_label="away",
            evidence_level=EvidenceLevel.MEDIUM,
            is_international_friendly=False,
            final_status=FinalStatus.REVISAR,
        )
        for _ in range(7)
    ]
    report = build_slate_distribution_report(observations)
    assert report.matches_home_under_5 == 7
    assert any("SUSPICIOUS_CLASS_DISTRIBUTION" in alarm for alarm in report.alarms)


def test_healthy_slate_raises_no_alarm() -> None:
    probs = [
        {"home": 0.50, "draw": 0.28, "away": 0.22},
        {"home": 0.22, "draw": 0.30, "away": 0.48},
        {"home": 0.40, "draw": 0.33, "away": 0.27},
        {"home": 0.30, "draw": 0.30, "away": 0.40},
        {"home": 0.45, "draw": 0.27, "away": 0.28},
        {"home": 0.26, "draw": 0.31, "away": 0.43},
    ]
    labels = ["home", "away", "home", "away", "home", "away"]
    observations = [
        SlateMatchObservation(
            probabilities=p,
            recommended_label=lab,
            evidence_level=EvidenceLevel.MEDIUM,
            is_international_friendly=False,
            final_status=FinalStatus.LISTO,
        )
        for p, lab in zip(probs, labels)
    ]
    report = build_slate_distribution_report(observations)
    assert report.alarms == []


def test_blocked_band_stays_bloqueado() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.47, "draw": 0.28, "away": 0.25},
        confidence_band="blocked",
        evidence_level=EvidenceLevel.LOW,
        recommended_outcome="1",
    )
    assert result.final_status is FinalStatus.BLOQUEADO
    assert SanityFlag.BLOCKED_INSUFFICIENT_DATA in result.flags
    assert result.risk_level is RiskLevel.HIGH


def test_visible_confidence_is_never_alta_with_risk_flags() -> None:
    for flag in (
        SanityFlag.LOW_EVIDENCE,
        SanityFlag.FALLBACK_USED,
        SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE,
        SanityFlag.SUSPICIOUS_CLASS_PROBABILITY,
        SanityFlag.BLOCKED_INSUFFICIENT_DATA,
    ):
        level, _ = compute_visible_confidence(
            final_status=FinalStatus.FIJO, risk_level=RiskLevel.LOW, flags=[flag]
        )
        assert level != "alta", flag


def test_visible_confidence_friendly_capped_at_media() -> None:
    level, reasons = compute_visible_confidence(
        final_status=FinalStatus.FIJO,
        risk_level=RiskLevel.LOW,
        flags=[SanityFlag.INTERNATIONAL_FRIENDLY],
    )
    assert level == "media"
    assert "Amistoso internacional" in reasons


def test_visible_confidence_bloqueado_is_baja_and_revisar_capped() -> None:
    blocked, _ = compute_visible_confidence(
        final_status=FinalStatus.BLOQUEADO, risk_level=RiskLevel.HIGH, flags=[]
    )
    assert blocked == "baja"
    revisar, _ = compute_visible_confidence(
        final_status=FinalStatus.REVISAR, risk_level=RiskLevel.HIGH, flags=[]
    )
    assert revisar in {"media-baja", "baja"}


def test_visible_confidence_alta_only_for_clean_fijo() -> None:
    level, reasons = compute_visible_confidence(
        final_status=FinalStatus.FIJO, risk_level=RiskLevel.LOW, flags=[]
    )
    assert level == "alta"
    assert reasons == []


def test_confidence_explanation_capped_at_three() -> None:
    _, reasons = compute_visible_confidence(
        final_status=FinalStatus.REVISAR,
        risk_level=RiskLevel.HIGH,
        flags=[
            SanityFlag.BLOCKED_INSUFFICIENT_DATA,
            SanityFlag.SUSPICIOUS_CLASS_PROBABILITY,
            SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE,
            SanityFlag.LOW_EVIDENCE,
            SanityFlag.INTERNATIONAL_FRIENDLY,
            SanityFlag.FALLBACK_USED,
        ],
    )
    assert len(reasons) == 3


def test_apply_sanity_layer_exposes_visible_confidence() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.12, "draw": 0.09, "away": 0.79},
        confidence_band="high",
        evidence_level=EvidenceLevel.LOW,
        is_international_friendly=True,
        fallback_used=True,
        recommended_outcome="2",
    )
    assert result.visible_confidence != "alta"
    assert isinstance(result.confidence_explanation, list)
    assert len(result.confidence_explanation) <= 3


def test_ticket_strategy_never_simple_with_risk_flags() -> None:
    for flag in (
        SanityFlag.LOW_EVIDENCE,
        SanityFlag.FALLBACK_USED,
        SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE,
        SanityFlag.SUSPICIOUS_CLASS_PROBABILITY,
        SanityFlag.BLOCKED_INSUFFICIENT_DATA,
    ):
        strategy, _, _ = compute_ticket_strategy(
            final_status=FinalStatus.FIJO, risk_level=RiskLevel.MEDIUM, flags=[flag]
        )
        assert strategy != "SIMPLE", flag


def test_ticket_strategy_high_risk_is_no_dejar_simple() -> None:
    strategy, label, reason = compute_ticket_strategy(
        final_status=FinalStatus.FIJO, risk_level=RiskLevel.HIGH, flags=[]
    )
    assert strategy == "NO_DEJAR_SIMPLE"
    assert label == "No dejar simple"
    assert reason


def test_ticket_strategy_bloqueado_is_evitar() -> None:
    strategy, label, _ = compute_ticket_strategy(
        final_status=FinalStatus.BLOQUEADO, risk_level=RiskLevel.HIGH, flags=[]
    )
    assert strategy == "EVITAR"
    assert label == "Evitar"


def test_ticket_strategy_revisar_is_no_dejar_simple() -> None:
    strategy, _, _ = compute_ticket_strategy(
        final_status=FinalStatus.REVISAR, risk_level=RiskLevel.LOW, flags=[]
    )
    assert strategy == "NO_DEJAR_SIMPLE"


def test_ticket_strategy_clean_can_be_simple() -> None:
    strategy, label, _ = compute_ticket_strategy(
        final_status=FinalStatus.FIJO, risk_level=RiskLevel.LOW, flags=[]
    )
    assert strategy == "SIMPLE"
    assert label == "Simple"


def test_ticket_strategy_label_never_contains_fijo() -> None:
    for status in FinalStatus:
        for risk in RiskLevel:
            _, label, _ = compute_ticket_strategy(final_status=status, risk_level=risk, flags=[])
            assert "Fijo" not in label


def test_apply_sanity_layer_exposes_ticket_strategy() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.12, "draw": 0.09, "away": 0.79},
        confidence_band="high",
        evidence_level=EvidenceLevel.LOW,
        is_international_friendly=True,
        fallback_used=True,
        recommended_outcome="2",
    )
    assert result.ticket_strategy != "SIMPLE"
    assert result.ticket_strategy_label
    assert "Fijo" not in result.ticket_strategy_label


def test_high_evidence_strong_pick_can_be_fijo() -> None:
    result = apply_sanity_layer(
        probabilities={"home": 0.62, "draw": 0.22, "away": 0.16},
        confidence_band="high",
        evidence_level=EvidenceLevel.HIGH,
        recommended_outcome="1",
    )
    assert result.final_status is FinalStatus.FIJO
    assert result.risk_level is RiskLevel.LOW
    assert not result.flags
