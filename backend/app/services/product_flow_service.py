"""Read-only product flow summary.

This layer ties the existing operational pieces together without changing
predictions, tickets, results or training state. It gives the UI one compact
contract for the daily workflow: active slate -> data state -> recommendation
and explanation -> ticket policy -> tracking/postmortem -> next action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.repositories.slate_repository import SlateRepository
from app.services.active_slate_scope import build_active_slate_scope
from app.services.completed_slate_results_validation_service import (
    build_completed_slates_validation,
)
from app.services.diagnostic_ttl_cache import cached_diagnostic_report
from app.services.learning_error_attribution_service import build_error_attribution
from app.services.learning_slate_scoring_service import LearningSlateScoringService
from app.services.money_mode_service import build_money_mode
from app.services.publication_gate_service import build_publication_gate_for_slate
from app.services.slate_classification_service import classify_slate
from app.services.slate_service import SlateService


def build_product_flow(session: Session, slate_id: str | None = None) -> dict[str, Any]:
    slate_service = SlateService(SlateRepository(session))
    active_scope = build_active_slate_scope(session)
    active_slates = [
        slate for info in active_scope if (slate := slate_service.get_slate(info.slate_id)) is not None
    ]
    selected = _select_slate(active_slates, slate_service, slate_id)
    key = (
        selected.id if selected is not None else None,
        tuple((slate.id, slate.composition_hash, slate.slate_version) for slate in active_slates),
    )
    return cached_diagnostic_report(
        "product_flow",
        key,
        lambda: _build_product_flow_uncached(session, slate_id, active_slates, selected),
    )


def _build_product_flow_uncached(
    session: Session,
    slate_id: str | None,
    active_slates: list[ProgolSlateModel],
    selected: ProgolSlateModel | None,
) -> dict[str, Any]:

    current = _current_slate_flow(session, selected, active_slates) if selected is not None else None
    postmortem = _postmortem_flow(session)
    steps = _workflow_steps(current, postmortem)
    actions = _next_actions(current, postmortem)

    return {
        "mode": "product_flow",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "active_upcoming" if slate_id is None else "selected_slate",
        "current_slate": current,
        "postmortem": postmortem,
        "workflow_steps": steps,
        "next_actions": actions,
        "write_safety": {"read_only": True, "writes_performed": False, "snapshots_created": False},
    }


def _select_slate(
    active_slates: list[ProgolSlateModel],
    slate_service: SlateService,
    slate_id: str | None,
) -> ProgolSlateModel | None:
    if slate_id:
        return slate_service.get_slate(slate_id)
    return active_slates[0] if active_slates else None


def _current_slate_flow(
    session: Session,
    slate: ProgolSlateModel,
    active_slates: list[ProgolSlateModel],
) -> dict[str, Any]:
    reality = classify_slate(session, slate)
    money = build_money_mode(session, slate)
    quality = _data_quality(money)
    recommendation = _recommendation(money)
    policy = _betting_policy(money, quality)
    publication_gate = build_publication_gate_for_slate(session, slate)
    active_contract = _active_slate_contract(slate, active_slates)
    drift = _drift_audit(reality, money, quality)
    return {
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "match_count": len(slate.matches),
            "is_archived": bool(slate.is_archived),
            "registration_closes_at": slate.registration_closes_at.isoformat()
            if slate.registration_closes_at
            else None,
            "classification": reality.classification.value,
            "classification_reasons": reality.reasons,
        },
        "results_loop": {
            "state": "awaiting_results" if not slate.is_archived else "closed",
            "next_step": "Cuando cierre, validar resultados oficiales y generar postmortem.",
            "automation_ready": True,
        },
        "data_quality": quality,
        "recommendation": recommendation,
        "betting_policy": policy,
        "publication_gate": publication_gate,
        "active_slate_contract": active_contract,
        "drift_audit": drift,
    }


def _data_quality(money: dict[str, Any]) -> dict[str, Any]:
    validation = money.get("validation", {})
    matches = list(money.get("matches", []) or [])
    blockers = list(validation.get("data_blockers", []) or [])
    warnings = list(validation.get("warnings", []) or [])
    no_simple = list(money.get("do_not_simple_positions", []) or [])
    review = list(money.get("must_review_positions", []) or [])
    total = max(1, int(money.get("slate", {}).get("match_count") or len(matches) or 1))
    score = 100
    score -= min(70, 28 * len(blockers))
    score -= min(24, 8 * len(warnings))
    score -= round((len(no_simple) / total) * 22)
    score -= round((len(review) / total) * 18)
    if validation.get("prediction_status") not in {"persisted", "live_available"}:
        score -= 35
    score = max(0, min(100, int(score)))
    if score >= 85:
        level = "excellent"
    elif score >= 70:
        level = "good"
    elif score >= 50:
        level = "watch"
    else:
        level = "blocked"
    return {
        "score": score,
        "level": level,
        "prediction_status": validation.get("prediction_status"),
        "blockers": blockers,
        "warnings": warnings,
        "no_simple_positions": no_simple,
        "must_review_positions": review,
        "summary": _quality_summary(level, blockers, warnings, no_simple, review),
    }


def _quality_summary(
    level: str,
    blockers: list[str],
    warnings: list[str],
    no_simple: list[int],
    review: list[int],
) -> str:
    if blockers:
        return "Bloqueada por datos: " + ", ".join(blockers)
    parts = [f"calidad {level}"]
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    if no_simple:
        parts.append(f"{len(no_simple)} posiciones no-simple")
    if review:
        parts.append(f"{len(review)} por revisar")
    return "; ".join(parts)


def _recommendation(money: dict[str, Any]) -> dict[str, Any]:
    decision = dict(money.get("decision", {}) or {})
    status = str(decision.get("status") or "NO_JUGAR")
    recommended_ticket = decision.get("recommended_ticket")
    return {
        "internal_score": {
            "money_mode_status": status,
            "confidence": decision.get("confidence"),
            "recommended_ticket": recommended_ticket,
        },
        "final_recommendation": "NO JUGAR" if status == "NO_JUGAR" else "JUGAR",
        "recommended_ticket": recommended_ticket,
        "explanation": {
            "primary_reason": decision.get("reason") or "",
            "risk_positions": money.get("do_not_simple_positions", []),
            "review_positions": money.get("must_review_positions", []),
            "why_not_play": decision.get("reason") if status == "NO_JUGAR" else None,
        },
    }


def _betting_policy(money: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    decision = money.get("decision", {})
    tickets = money.get("tickets", {})
    recommended = decision.get("recommended_ticket")
    ticket = tickets.get(recommended or "", {}) if isinstance(tickets, dict) else {}
    hard_no_play = decision.get("status") == "NO_JUGAR" or quality["level"] == "blocked"
    if hard_no_play:
        action = "do_not_play"
    elif recommended == "conservative":
        action = "play_minimum_conservative"
    elif recommended == "balanced":
        action = "play_balanced"
    else:
        action = "simulate_only"
    return {
        "action": action,
        "hard_no_play": hard_no_play,
        "recommended_ticket": recommended,
        "max_combinations": ticket.get("estimated_combinations"),
        "estimated_cost": ticket.get("estimated_cost"),
        "cost_note": ticket.get("cost_note"),
        "limits": [
            "Nunca jugar si Money Mode dice NO_JUGAR.",
            "No convertir posiciones NO SIMPLE en fijo.",
            "Usar conservador si el balanceado deja riesgo sin cobertura.",
            "Costo real solo cuando el precio esté verificado.",
        ],
    }


def _active_slate_contract(
    slate: ProgolSlateModel,
    active_slates: list[ProgolSlateModel],
) -> dict[str, Any]:
    by_week_type: dict[str, int] = {}
    violations: list[str] = []
    for item in active_slates:
        by_week_type[item.week_type] = by_week_type.get(item.week_type, 0) + 1
        if item.is_archived:
            violations.append(f"{item.draw_code}: archived_visible")
        if not item.matches:
            violations.append(f"{item.draw_code}: no_matches")
    if slate.is_archived:
        violations.append(f"{slate.draw_code}: selected_archived_read_only")
    return {
        "active_count": len(active_slates),
        "by_week_type": by_week_type,
        "selected_is_active": any(item.id == slate.id for item in active_slates),
        "strict": len(violations) == 0,
        "violations": violations,
    }


def _drift_audit(reality: Any, money: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    validation = money.get("validation", {})
    signals: list[str] = []
    if not reality.has_official_proposal:
        signals.append("missing_official_ln_lineage")
    if validation.get("prediction_status") not in {"persisted", "live_available"}:
        signals.append("prediction_source_missing")
    if quality["score"] < 70:
        signals.append("low_data_quality")
    if validation.get("data_blockers"):
        signals.extend(str(item) for item in validation.get("data_blockers", []))
    return {
        "status": "clear" if not signals else "watch",
        "signals": sorted(set(signals)),
        "competitions": reality.competitions,
        "source_url": reality.source_url,
        "classification": reality.classification.value,
    }


def _postmortem_flow(session: Session) -> dict[str, Any]:
    return cached_diagnostic_report(
        "product_flow_postmortem",
        "all_completed_slates",
        lambda: _postmortem_flow_uncached(session),
    )


def _postmortem_flow_uncached(session: Session) -> dict[str, Any]:
    validation = build_completed_slates_validation(session)
    slates = list(validation.get("slates", []) or [])
    latest = slates[0] if slates else None
    scored = None
    attribution = None
    if latest is not None:
        slate = SlateRepository(session).get_slate(str(latest["slate_id"]))
        if slate is not None:
            try:
                scored = LearningSlateScoringService(session).score_slate(slate)
                attribution = build_error_attribution(session, slate)
            except Exception as exc:  # pragma: no cover - diagnostic only
                scored = {"error": str(exc)}
    return {
        "completed_slate_count": validation.get("slate_count", 0),
        "ready_to_apply_count": validation.get("ready_count", 0),
        "latest_validation": latest,
        "latest_score": scored,
        "latest_error_attribution": attribution,
        "next_step": _postmortem_next_step(latest),
        "write_safety": {"read_only": True, "writes_performed": False},
    }


def _postmortem_next_step(latest: dict[str, Any] | None) -> str:
    if latest is None:
        return "Esperar una slate cerrada con predicciones."
    if latest.get("ready_to_apply"):
        return "Aplicar resultados oficiales con confirmación explícita y generar reporte de aprendizaje."
    blockers = latest.get("blockers") or []
    if blockers:
        return "Resolver validación de resultados: " + ", ".join(str(item) for item in blockers)
    return "Revisar score y atribución de errores."


def _workflow_steps(current: dict[str, Any] | None, postmortem: dict[str, Any]) -> list[dict[str, Any]]:
    current_ready = current is not None
    data_ok = False
    recommendation = "NO DATA"
    ticket_status = "blocked"
    if current is not None:
        data_ok = current["data_quality"]["level"] in {"excellent", "good"}
        recommendation = current["recommendation"]["final_recommendation"]
        ticket_status = current["betting_policy"]["action"]
    return [
        {"step": "concurso_actual", "status": "ready" if current_ready else "blocked"},
        {"step": "estado_de_datos", "status": "ready" if data_ok else "watch"},
        {"step": "recomendacion", "status": recommendation.lower().replace(" ", "_")},
        {"step": "boleto", "status": ticket_status},
        {"step": "seguimiento", "status": "ready"},
        {"step": "postmortem", "status": "ready" if postmortem.get("latest_validation") else "pending"},
    ]


def _next_actions(current: dict[str, Any] | None, postmortem: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if current is None:
        actions.append("Cargar o descubrir el concurso oficial vigente.")
    else:
        if current["data_quality"]["blockers"]:
            actions.append("Resolver bloqueadores de datos antes de recomendar boleto.")
        gate = current.get("publication_gate") or {}
        if gate.get("status") == "DO_NOT_PLAY":
            actions.append(str(gate.get("reason") or "Gate de publicación bloqueado."))
        if current["drift_audit"]["status"] != "clear":
            actions.append("Revisar drift/lineage antes de confiar en la recomendación.")
        if current["betting_policy"]["hard_no_play"]:
            actions.append("No jugar; usar opciones solo como simulación.")
        else:
            actions.append(f"Ejecutar política: {current['betting_policy']['action']}.")
    actions.append(str(postmortem.get("next_step")))
    return actions
