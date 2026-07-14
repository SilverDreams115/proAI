"""R7.0 — Learning error attribution (read-only).

Given a scored, comparable slate it explains *why* each prediction was right or
wrong and whether the guardrails / Money Mode behaved correctly. It answers, per
match: what failed, why it failed, what data was missing, whether the system
should have blocked it, and whether Money Mode was right to say NO JUGAR.

The per-position classifier (``classify_position``) is a pure function so the
scoring service can reuse it without importing this module's DB code. Writes
nothing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel

# The full error taxonomy this service can emit.
ERROR_TYPES = (
    "correct",
    "wrong_favorite",
    "draw_underestimated",
    "favorite_overestimated",
    "away_overestimated",
    "low_evidence_correctly_blocked",
    "guardrail_saved",
    "guardrail_missed",
    "rating_helped",
    "rating_hurt",
    "canary_helped",
    "canary_hurt",
    "money_mode_correctly_blocked",
    "money_mode_too_conservative",
    "data_quality_issue",
    "placeholder_issue",
    "result_conflict",
)

# final_status buckets where the guardrail actively suppressed a simple pick.
_GUARDRAIL_ENGAGED = {"BLOQUEADO", "REVISAR"}


def guardrail_status(final_status: str | None) -> str:
    return {
        "BLOQUEADO": "blocked",
        "REVISAR": "no_simple",
        "FIJO": "fijo",
        "LISTO": "ready",
    }.get((final_status or "").upper(), "unknown")


def classify_position(
    *,
    prediction_sign: str | None,
    actual_sign: str | None,
    decision_probs: dict[str, float] | None,
    effective_probs: dict[str, float] | None = None,
    final_status: str | None = None,
    evidence_level: str | None = None,
    money_blocked: bool = False,
    has_prediction: bool = True,
    has_result: bool = True,
    result_conflict: bool = False,
) -> dict[str, Any]:
    """Classify a single position. Pure function, no DB access."""
    if result_conflict:
        return _result(
            "result_conflict", "resultado en conflicto entre fuentes",
            missing_data=True, should_have_blocked=False, money_mode_correct=None,
        )
    if not has_result:
        return _result(
            "data_quality_issue", "sin resultado canónico disponible",
            missing_data=True, should_have_blocked=False, money_mode_correct=None,
        )
    if not has_prediction or not decision_probs:
        return _result(
            "data_quality_issue", "sin predicción/probabilidades comparables",
            missing_data=True, should_have_blocked=False, money_mode_correct=None,
        )

    status = (final_status or "").upper()
    engaged = status in _GUARDRAIL_ENGAGED
    low_evidence = (evidence_level or "").lower() == "low"
    hit = prediction_sign == actual_sign
    favorite = max(decision_probs, key=lambda k: decision_probs[k])
    p_pred = decision_probs.get(prediction_sign or "", 0.0)

    canary_label: str | None = None
    if effective_probs:
        eff_pred = max(effective_probs, key=lambda k: effective_probs[k])
        eff_hit = eff_pred == actual_sign
        if eff_hit and not hit:
            canary_label = "canary_helped"
        elif hit and not eff_hit:
            canary_label = "canary_hurt"

    # --- Money Mode dimension (independent of the signal-level error_type). A
    # NO-JUGAR slate blocks every pick, so this is tracked separately so the
    # underlying model/guardrail error is never masked. ---
    money_mode_label: str | None = None
    money_mode_correct: bool | None = None
    if money_blocked:
        if hit:
            money_mode_label, money_mode_correct = "money_mode_too_conservative", False
        else:
            money_mode_label, money_mode_correct = "money_mode_correctly_blocked", True

    # --- Signal-level error_type (the model/guardrail story) ---
    if hit:
        if engaged:
            error_type, reason = (
                "guardrail_missed",
                f"acierto que el guardrail marcó como no-simple ({guardrail_status(status)})",
            )
        elif canary_label == "canary_helped":
            error_type, reason = "canary_helped", "acierto gracias al ajuste canary"
        else:
            error_type, reason = "correct", "acierto"
        return _result(
            error_type, reason, should_have_blocked=False,
            money_mode_label=money_mode_label, money_mode_correct=money_mode_correct,
        )

    # ----- miss -----
    if low_evidence and engaged:
        error_type, reason = (
            "low_evidence_correctly_blocked",
            "fallo en pick de baja evidencia que el guardrail bloqueó",
        )
        should_blocked = True
    elif engaged:
        error_type, reason = (
            "guardrail_saved",
            f"fallo en pick no-simple que el guardrail degradó ({guardrail_status(status)})",
        )
        should_blocked = True
    elif canary_label == "canary_hurt":
        error_type, reason = (
            "canary_hurt", "el ajuste canary empeoró un pick que habría acertado",
        )
        should_blocked = True
    elif actual_sign == "E":
        error_type, reason = "draw_underestimated", "empate subestimado (prob asignada al empate baja)"
        should_blocked = True
    elif prediction_sign == favorite and p_pred >= 0.5:
        error_type, reason = (
            "favorite_overestimated",
            f"favorito sobrestimado (prob {p_pred:.2f}) que no se cumplió",
        )
        should_blocked = True
    elif prediction_sign == "V":
        error_type, reason = "away_overestimated", "visita sobrestimada que no se cumplió"
        should_blocked = True
    else:
        error_type, reason = "wrong_favorite", "favorito equivocado sin guardrail que lo detuviera"
        should_blocked = True

    # An unguarded, un-blocked losing simple pick is the only true "should have
    # blocked" case — if the guardrail or Money Mode already caught it, the
    # system did the right thing.
    should_blocked = should_blocked and not engaged and not money_blocked
    return _result(
        error_type, reason, should_have_blocked=should_blocked,
        money_mode_label=money_mode_label, money_mode_correct=money_mode_correct,
    )


def _result(
    error_type: str,
    reason: str,
    *,
    missing_data: bool = False,
    should_have_blocked: bool = False,
    money_mode_label: str | None = None,
    money_mode_correct: bool | None = None,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "reason": reason,
        "missing_data": missing_data,
        "should_have_blocked": should_have_blocked,
        "money_mode_label": money_mode_label,
        "money_mode_decision_correct": money_mode_correct,
    }


def build_error_attribution(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Slate-level error attribution built on top of the scoring detail."""
    from app.services.learning_slate_scoring_service import LearningSlateScoringService

    score = LearningSlateScoringService(session).score_slate(slate)

    by_type: dict[str, int] = {}
    by_money_mode: dict[str, int] = {}
    misses: list[dict[str, Any]] = []
    guardrail_saved = 0
    guardrail_missed = 0
    money_correct = 0
    money_too_conservative = 0
    should_have_blocked = 0

    for pos in score["by_position"]:
        et = pos["error_type"]
        by_type[et] = by_type.get(et, 0) + 1
        mm = pos.get("money_mode_label")
        if mm:
            by_money_mode[mm] = by_money_mode.get(mm, 0) + 1
        if et in ("guardrail_saved", "low_evidence_correctly_blocked"):
            guardrail_saved += 1
        if et == "guardrail_missed":
            guardrail_missed += 1
        if mm == "money_mode_correctly_blocked":
            money_correct += 1
        if mm == "money_mode_too_conservative":
            money_too_conservative += 1
        if pos.get("should_have_blocked"):
            should_have_blocked += 1
        if pos["hit"] is False:
            misses.append(
                {
                    "position": pos["position"],
                    "prediction": pos["prediction"],
                    "actual": pos["actual"],
                    "error_type": et,
                    "reason": pos.get("reason"),
                    "guardrail_status": pos.get("guardrail_status"),
                    "was_money_mode_blocked": pos.get("was_money_mode_blocked"),
                }
            )

    return {
        "mode": "learning_error_attribution",
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "comparable": score["comparable"],
        "summary": {
            "by_error_type": by_type,
            "by_money_mode": by_money_mode,
            "guardrail_saved": guardrail_saved,
            "guardrail_missed": guardrail_missed,
            "money_mode_correctly_blocked": money_correct,
            "money_mode_too_conservative": money_too_conservative,
            "should_have_blocked": should_have_blocked,
        },
        "misses": misses,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
