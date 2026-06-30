"""Presentation guard (R5.6-D).

Pure, read-only derivation that stops the UI from advertising a *dangerous*
prediction as a simple, playable pick. It changes nothing about the
probabilities or the persisted prediction — it only summarises the existing
sanity metadata into an explicit, non-contradictory presentation contract so a
match flagged "No dejar simple / Riesgo alto" can never render as
"Sugerencia: V" (a simple suggestion).

The single authoritative "playable simple" signal is the sanity ticket
strategy: only ``SIMPLE`` is a defensible single pick; every other strategy
(``DOBLE_RECOMENDADO``, ``TRIPLE_RECOMENDADO``, ``NO_DEJAR_SIMPLE``,
``EVITAR``) is by definition not simple. On top of that, several independent
risk conditions force ``simple_allowed=False`` and are surfaced as reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Map the L/E/V ("1"/"X"/"2") outcome code to the user-facing signal letter.
_SIGNAL_BY_CODE = {"1": "L", "X": "E", "2": "V"}

# Only this ticket strategy is a defensible simple pick.
_SIMPLE_STRATEGY = "SIMPLE"

# Final-status buckets that must never read as a simple suggestion.
_REVIEW_STATUSES = frozenset({"REVISAR"})
_BLOCKED_STATUSES = frozenset({"BLOQUEADO"})

# Sanity flags that, on their own, make a simple pick misleading.
_SUSPICIOUS_FLAG = "SUSPICIOUS_CLASS_PROBABILITY"
_LOW_EVIDENCE_FLAG = "LOW_EVIDENCE"


@dataclass(frozen=True)
class PresentationGuard:
    simple_allowed: bool
    primary_signal: str
    recommendation_label: str
    risk_level: str
    confidence: str
    reason: list[str] = field(default_factory=list)
    presentation_confidence_band: str = "review"
    presentation_confidence_reason: str = ""


# Allowed UI confidence labels (most→least trustworthy, plus the two gated ones).
_PRESENTATION_BANDS = frozenset({"high", "medium", "low", "review", "blocked", "unreliable"})


def derive_presentation_confidence(
    *,
    confidence_band: str,
    final_status: str,
    flags: list[str] | None,
    simple_allowed: bool,
    draw_calibration_applied: bool = False,
) -> tuple[str, str]:
    """Degrade the model band into a UI-safe presentation band.

    The model ``confidence_band`` drives internal logic and is left intact; this
    is the label the UI shows so a capped / flagged / non-playable pick never
    reads as "high / alta confianza". Returns (band, reason).
    """
    band = str(confidence_band or "low").lower()
    status = str(final_status).upper()
    flag_set = {str(f).upper() for f in (flags or [])}
    cap = "EXTREME_PROBABILITY_CAPPED" in flag_set
    extreme_no_ev = "EXTREME_PROBABILITY_WITHOUT_EVIDENCE" in flag_set
    low_ev = "LOW_EVIDENCE" in flag_set
    fallback = "FALLBACK_USED" in flag_set

    # 1. Hard-blocked always reads blocked (regardless of model band).
    if status in _BLOCKED_STATUSES:
        return "blocked", "bloqueado por datos insuficientes"
    # 2. Unanchored fallback with an extreme, capped, non-playable pick.
    if fallback and extreme_no_ev and low_ev and not simple_allowed:
        return "unreliable", "fallback sin evidencia con probabilidad extrema"
    # 3. Flagged for review.
    if status in _REVIEW_STATUSES:
        return "review", "marcado para revisión; probabilidad ajustada"
    # 4. Probability was capped / extreme-without-evidence.
    if cap or extreme_no_ev:
        return "review", "probabilidad ajustada por baja evidencia"
    # 5. Low evidence / fallback / draw-calibrated → never above "low".
    if low_ev or draw_calibration_applied or (fallback and band == "high"):
        return "low", "baja evidencia"
    # 6. A high model band that is not a defensible simple pick must not show "high".
    if band == "high" and not simple_allowed:
        return "review", "no firmable como simple"
    # 7. Passthrough a clean model band.
    if band in _PRESENTATION_BANDS:
        return band, "confianza del modelo"
    return "review", "banda desconocida"


def derive_presentation_guard(
    *,
    recommended_outcome: str,
    final_status: str,
    risk_level: str,
    ticket_strategy: str,
    flags: list[str] | None,
    fallback_used: bool,
    visible_confidence: str,
    confidence_band: str = "low",
    draw_calibration_applied: bool = False,
) -> PresentationGuard:
    """Summarise existing sanity metadata into a non-contradictory contract."""
    primary_signal = _SIGNAL_BY_CODE.get(str(recommended_outcome), str(recommended_outcome))
    status = str(final_status).upper()
    strategy = str(ticket_strategy).upper()
    risk = str(risk_level).lower()
    flag_set = {str(f).upper() for f in (flags or [])}

    reasons: list[str] = []
    if status in _BLOCKED_STATUSES:
        reasons.append("blocked")
    if status in _REVIEW_STATUSES:
        reasons.append("review")
    if risk == "high":
        reasons.append("risk_high")
    if strategy.startswith("NO_DEJAR"):
        reasons.append("no_dejar_simple")
    if strategy not in (_SIMPLE_STRATEGY,) and "no_dejar_simple" not in reasons:
        # DOBLE / TRIPLE / EVITAR — not dangerous per se, but not a simple pick.
        reasons.append("requires_coverage")
    if _SUSPICIOUS_FLAG in flag_set:
        reasons.append("suspicious_class")
    if fallback_used and _LOW_EVIDENCE_FLAG in flag_set:
        reasons.append("fallback_low_evidence")

    # Authoritative: only a SIMPLE strategy with no risk reason is playable simple.
    simple_allowed = strategy == _SIMPLE_STRATEGY and not reasons

    if status in _BLOCKED_STATUSES:
        recommendation_label = "BLOQUEADO"
    elif simple_allowed:
        recommendation_label = "SIMPLE"
    else:
        recommendation_label = "NO SIMPLE"

    # De-duplicate while preserving order.
    ordered_reasons = list(dict.fromkeys(reasons))

    pres_band, pres_reason = derive_presentation_confidence(
        confidence_band=confidence_band,
        final_status=final_status,
        flags=flags,
        simple_allowed=simple_allowed,
        draw_calibration_applied=draw_calibration_applied,
    )

    return PresentationGuard(
        simple_allowed=simple_allowed,
        primary_signal=primary_signal,
        recommendation_label=recommendation_label,
        risk_level=risk,
        confidence=str(visible_confidence),
        reason=ordered_reasons,
        presentation_confidence_band=pres_band,
        presentation_confidence_reason=pres_reason,
    )
