"""Unified publication / betting gate for active Progol slates.

Read-only service. It does not train, persist predictions, save tickets, apply
results, mutate slates or relax any model threshold. It collects the existing
readiness, Money Mode and learning dataset audits into one stable contract that
operators and UI can use for every future slate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.repositories.slate_repository import SlateRepository
from app.services.active_slate_scope import build_active_slate_scope
from app.services.learning_dataset_readiness_service import build_dataset_readiness
from app.services.money_mode_service import build_money_mode
from app.services.slate_readiness_report_service import build_slate_readiness_report
from app.services.slate_service import SlateService


_BLOCKING_STATUSES = {"BLOQUEADO"}
_TEAM_BLOCKERS = {"team_resolution"}


def build_publication_gate(session: Session, *, slate_id: str | None = None) -> dict[str, Any]:
    """Build the unified gate for one slate or every active/upcoming slate."""
    slate_service = SlateService(SlateRepository(session))
    slates = _select_slates(session, slate_service, slate_id)
    readiness = build_slate_readiness_report(
        session,
        include_archived=True,
        slate_ids={slate.id for slate in slates},
    )
    learning = build_dataset_readiness(session)

    readiness_by_id = {
        str(item.get("slate_id")): item for item in readiness.get("slates", []) or []
    }
    slate_reports = [
        _build_slate_gate(session, slate, readiness_by_id.get(slate.id, {}), learning)
        for slate in slates
    ]
    summary = _summary(slate_reports, learning)

    return {
        "mode": "publication_gate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "selected_slate" if slate_id else "active_upcoming",
        "selected_slate_id": slate_id,
        "summary": summary,
        "slates": slate_reports,
        "learning_gate": {
            "training_ready": bool(learning.get("training_ready")),
            "classification_training_ready": bool(learning.get("classification_training_ready")),
            "reason": learning.get("reason"),
            "minimum_missing": learning.get("minimum_missing") or [],
            "recommended_next_data_action": learning.get("recommended_next_data_action"),
            "comparable_slate_count": learning.get("comparable_slate_count"),
            "comparable_match_count": learning.get("comparable_match_count"),
            "thresholds": learning.get("thresholds") or {},
        },
        "write_safety": {"read_only": True, "writes_performed": False, "snapshots_created": False},
    }


def build_publication_gate_for_slate(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    report = build_publication_gate(session, slate_id=slate.id)
    slates = list(report.get("slates") or [])
    return slates[0] if slates else {}


def _select_slates(
    session: Session,
    slate_service: SlateService,
    slate_id: str | None,
) -> list[ProgolSlateModel]:
    if slate_id:
        slate = slate_service.get_slate(slate_id)
        return [slate] if slate is not None else []
    selected: list[ProgolSlateModel] = []
    for info in build_active_slate_scope(session):
        slate = slate_service.get_slate(info.slate_id)
        if slate is not None:
            selected.append(slate)
    return selected


def _build_slate_gate(
    session: Session,
    slate: ProgolSlateModel,
    readiness: dict[str, Any],
    learning: dict[str, Any],
) -> dict[str, Any]:
    money = build_money_mode(session, slate)
    readiness_matches = list(readiness.get("matches") or [])
    blocked = _blocked_positions(readiness_matches)
    warnings = _warning_positions(readiness_matches)
    placeholders = _placeholder_positions(readiness_matches)
    data_blockers = list((money.get("validation") or {}).get("data_blockers") or [])
    money_decision = dict(money.get("decision") or {})
    learning_exclusion = (learning.get("excluded") or {}).get(slate.draw_code)

    status = _status(
        money_status=str(money_decision.get("status") or "NO_JUGAR"),
        blocked_count=len(blocked),
        placeholder_count=len(placeholders),
        data_blockers=data_blockers,
    )
    data_debt = _data_debt(
        blocked=blocked,
        warnings=warnings,
        placeholders=placeholders,
        data_blockers=data_blockers,
        learning_exclusion=learning_exclusion,
    )

    return {
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "match_count": len(slate.matches),
            "registration_closes_at": slate.registration_closes_at.isoformat()
            if slate.registration_closes_at
            else None,
            "composition_hash": slate.composition_hash,
            "slate_version": slate.slate_version,
        },
        "status": status,
        "publish_allowed": status in {"READY_TO_PLAY", "PLAY_CONSERVATIVE_ONLY"},
        "whatsapp_allowed": status in {"READY_TO_PLAY", "PLAY_CONSERVATIVE_ONLY"},
        "money_mode_status": money_decision.get("status"),
        "recommended_ticket": money_decision.get("recommended_ticket"),
        "reason": _reason(status, money_decision, data_debt),
        "data_debt": data_debt,
        "ml_activation_gate": {
            "activation_allowed": False,
            "training_ready": bool(learning.get("training_ready")),
            "classification_training_ready": bool(learning.get("classification_training_ready")),
            "reason": (
                "ML activation remains blocked until clean comparable evidence passes the training gate."
                if not learning.get("training_ready")
                else "Training can be proposed, but activation still requires shadow validation."
            ),
        },
        "next_actions": _next_actions(status, data_debt, learning),
    }


def _status(
    *,
    money_status: str,
    blocked_count: int,
    placeholder_count: int,
    data_blockers: list[str],
) -> str:
    if data_blockers or placeholder_count or blocked_count or money_status == "NO_JUGAR":
        return "DO_NOT_PLAY"
    if money_status == "JUGAR_SOLO_CONSERVADOR":
        return "PLAY_CONSERVATIVE_ONLY"
    if money_status == "JUGAR_BALANCEADO":
        return "READY_TO_PLAY"
    return "REVIEW_REQUIRED"


def _blocked_positions(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches:
        flags = set(match.get("flags") or [])
        blockers = set(match.get("actionable_blockers") or [])
        if (
            str(match.get("status") or "") in _BLOCKING_STATUSES
            or "BLOCKED_INSUFFICIENT_DATA" in flags
            or blockers & _TEAM_BLOCKERS
        ):
            rows.append(_match_ref(match))
    return rows


def _warning_positions(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _match_ref(match)
        for match in matches
        if str(match.get("status") or "") == "REVISAR"
    ]


def _placeholder_positions(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _match_ref(match)
        for match in matches
        if "PLACEHOLDER_TEAM" in set(match.get("flags") or [])
        or "team_resolution" in set(match.get("actionable_blockers") or [])
    ]


def _match_ref(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": match.get("position"),
        "match": match.get("match"),
        "status": match.get("status"),
        "flags": match.get("flags") or [],
        "actionable_blockers": match.get("actionable_blockers") or [],
    }


def _data_debt(
    *,
    blocked: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    data_blockers: list[str],
    learning_exclusion: str | None,
) -> dict[str, Any]:
    return {
        "blocked_count": len(blocked),
        "warning_count": len(warnings),
        "placeholder_count": len(placeholders),
        "data_blockers": data_blockers,
        "learning_exclusion": learning_exclusion,
        "blocked_positions": blocked,
        "warning_positions": warnings,
        "placeholder_positions": placeholders,
    }


def _reason(status: str, money_decision: dict[str, Any], data_debt: dict[str, Any]) -> str:
    if status == "DO_NOT_PLAY":
        if data_debt["placeholder_count"]:
            return f"Resolver {data_debt['placeholder_count']} placeholder(s) antes de jugar."
        if data_debt["blocked_count"]:
            return f"Resolver {data_debt['blocked_count']} posición(es) bloqueada(s) antes de jugar."
        if data_debt["data_blockers"]:
            return "Bloqueadores de datos: " + ", ".join(data_debt["data_blockers"])
        return str(money_decision.get("reason") or "Money Mode no autoriza jugar.")
    if status == "PLAY_CONSERVATIVE_ONLY":
        return str(money_decision.get("reason") or "Jugar solo la opción conservadora.")
    if status == "READY_TO_PLAY":
        return str(money_decision.get("reason") or "Gate listo para boleto recomendado.")
    return "Requiere revisión operativa antes de publicar."


def _next_actions(
    status: str,
    data_debt: dict[str, Any],
    learning: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if data_debt["placeholder_count"]:
        actions.append("Resolver alias/equipos placeholder antes de publicar la slate.")
    if data_debt["blocked_count"]:
        actions.append("Reforzar cobertura de datos en posiciones BLOQUEADO.")
    if data_debt["warning_count"]:
        actions.append("Revisión humana obligatoria para posiciones REVISAR.")
    if data_debt["learning_exclusion"]:
        actions.append(f"Completar resultados oficiales para aprendizaje: {data_debt['learning_exclusion']}.")
    if not learning.get("training_ready"):
        actions.append(str(learning.get("recommended_next_data_action")))
    if not actions:
        actions.append("Mantener monitoreo de cierre, resultados y drift.")
    if status == "DO_NOT_PLAY":
        actions.insert(0, "No jugar esta slate con dinero real.")
    return actions


def _summary(slate_reports: list[dict[str, Any]], learning: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for slate in slate_reports:
        status = str(slate.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return {
        "slate_count": len(slate_reports),
        "status_counts": counts,
        "playable_count": counts.get("READY_TO_PLAY", 0) + counts.get("PLAY_CONSERVATIVE_ONLY", 0),
        "do_not_play_count": counts.get("DO_NOT_PLAY", 0),
        "ml_training_ready": bool(learning.get("training_ready")),
    }
