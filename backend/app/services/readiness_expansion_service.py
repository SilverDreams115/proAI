"""R6.3 — Readiness expansion audit (read-only, changes no state).

Explains, per slate fixture, *why* a match is not READY and what real datum
would unblock it — without ever promoting a match or relaxing a guardrail. It
derives everything from existing signals (the Money Mode presentation guard, the
competition context, and the free-results provider dry-run); it never lowers a
threshold, hides LOW_EVIDENCE, or turns a low-evidence friendly into READY.

``safe_to_promote_now`` is true only when the match is *already* a defensible
confident pick (the guard allows a simple) — i.e. the evidence is already
sufficient. So this audit never invents a promotion; in the current
all-friendlies slates it honestly reports zero safe promotions.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.services.money_mode_service import build_money_mode
from app.services.results_provider_service import build_slate_results_dry_run

# Reason (presentation-guard) -> readiness blocker category.
_REASON_CATEGORY = {
    "blocked": "stale_metadata",
    "review": "suspicious_class",
    "risk_high": "low_evidence",
    "no_dejar_simple": "low_evidence",
    "requires_coverage": "low_evidence",
    "suspicious_class": "suspicious_class",
    "fallback_low_evidence": "fallback_used",
}

# Blocker category -> the real datum that would unblock it.
_IMPROVED_BY = {
    "low_evidence": "more_result_history",
    "fallback_used": "no_rating",
    "suspicious_class": "better_calibration",
    "stale_metadata": "fix_metadata_or_mapping",
    "friendly_context": "friendly_specific_calibrator",
    "placeholder_team": "resolve_fixture_team",
    "provider_unmatched": "provider_finished_result",
    "canary_not_active": "partial_rating",
    "no_result_history": "more_result_history",
}

_FRIENDLY_TOKENS = ("friendly", "amistoso", "friendlies")


def _is_friendly(competition_name: str | None) -> bool:
    if not competition_name:
        return False
    lowered = competition_name.lower()
    return any(token in lowered for token in _FRIENDLY_TOKENS)


def build_ready_expansion(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Per-match readiness audit for one slate (read-only)."""
    money = build_money_mode(session, slate)
    provider = build_slate_results_dry_run(slate)

    money_by_pos = {m["position"]: m for m in money.get("matches", [])}
    provider_by_pos = {m["position"]: m for m in provider.get("matches", [])}
    competition_by_pos = {
        link.position: getattr(link.match.competition, "name", None)
        for link in slate.matches
    }

    rows: list[dict[str, Any]] = []
    ready_now = 0
    potential_with_external = 0

    for link in sorted(slate.matches, key=lambda item: item.position):
        pos = link.position
        mm = money_by_pos.get(pos, {})
        pv = provider_by_pos.get(pos, {})
        simple_allowed = bool(mm.get("simple_allowed"))
        reasons = list(mm.get("reason", []))
        competition = competition_by_pos.get(pos)

        blocked_by: list[str] = []
        for reason in reasons:
            category = _REASON_CATEGORY.get(reason)
            if category and category not in blocked_by:
                blocked_by.append(category)
        if _is_friendly(competition) and "friendly_context" not in blocked_by:
            blocked_by.append("friendly_context")
        # Provider can supply a finished result as secondary evidence when the
        # local fixture has none matched at high confidence.
        if pv.get("confidence") != "high" or pv.get("status") != "finished":
            if "provider_unmatched" not in blocked_by:
                blocked_by.append("provider_unmatched")
        if not bool(mm.get("canary_active")) and "canary_not_active" not in blocked_by:
            blocked_by.append("canary_not_active")

        improvements: list[str] = []
        for category in blocked_by:
            improved = _IMPROVED_BY.get(category)
            if improved and improved not in improvements:
                improvements.append(improved)

        current_status = "READY" if simple_allowed else "NOT_READY"
        if simple_allowed:
            ready_now += 1
        # Would a provider finished result add usable secondary evidence?
        if pv.get("status") == "finished" and pv.get("confidence") in ("high", "medium"):
            potential_with_external += 1

        # Safe to promote ONLY if already a defensible confident pick. This never
        # invents READY for a blocked/low-evidence match.
        safe = simple_allowed and not blocked_by

        rows.append(
            {
                "position": pos,
                "match": mm.get("match") or f"pos {pos}",
                "competition": competition,
                "current_status": current_status,
                "blocked_by": blocked_by,
                "can_be_improved_by": improvements,
                "safe_to_promote_now": safe,
            }
        )

    safe_promotions = [r["position"] for r in rows if r["safe_to_promote_now"]]
    no_promote_reason = (
        "Ninguna promoción READY segura: cada partido sigue con evidencia "
        "insuficiente, fallback o contexto de amistoso, y promover sin datos "
        "reales falsearía la confianza."
        if not safe_promotions
        else f"{len(safe_promotions)} partido(s) ya tienen evidencia suficiente."
    )

    return {
        "mode": "readiness_expansion_audit",
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "match_count": len(slate.matches),
        },
        "ready_now": ready_now,
        "ready_potential_with_external_data": ready_now + potential_with_external,
        "ready_potential_after_provider_results": ready_now,
        "safe_promotions": safe_promotions,
        "no_promote_reason": no_promote_reason,
        "provider_status": provider.get("status"),
        "matches": rows,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_active_slates_ready_expansion(session: Session) -> dict[str, Any]:
    from app.repositories.slate_repository import SlateRepository
    from app.services.active_slate_scope import build_active_slate_scope
    from app.services.slate_service import SlateService

    slate_service = SlateService(SlateRepository(session))
    out: list[dict[str, Any]] = []
    for info in build_active_slate_scope(session):
        slate = slate_service.get_slate(info.slate_id)
        if slate is not None:
            out.append(build_ready_expansion(session, slate))
    total_safe = sum(len(s["safe_promotions"]) for s in out)
    return {
        "mode": "readiness_expansion_audit_active_upcoming",
        "scope": "active_upcoming",
        "slate_count": len(out),
        "total_safe_promotions": total_safe,
        "no_promote_reason": (
            "No hay promociones READY seguras en esta fase."
            if total_safe == 0
            else f"{total_safe} promoción(es) segura(s)."
        ),
        "slates": out,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
