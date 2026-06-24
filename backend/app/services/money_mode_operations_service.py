"""R6.1 — Money Mode operational readiness (strictly read-only).

The single operational layer used for daily Progol review of every active/
upcoming slate. It orchestrates the existing read-only building blocks in a
fixed order — active-slate scope -> slate validation -> ticket canary dry-run ->
Money Mode -> write-safety audit -> counts before/after — and emits one
self-describing payload per slate plus a compact operational status.

It writes nothing: it reuses the Money Mode / dry-run / validation services
(all ``persist=False`` / ``build_read_only``) and is always invoked inside a
``read_only_transaction`` by its callers (CLI + endpoint). The counts
before/after are captured around the run to *prove* delta-zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchFeatureSnapshotModel,
    MatchModel,
    MatchResultModel,
    ModelTrainingRunModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)
from app.models.team_rating import TeamRatingRunModel, TeamRatingSnapshotModel
from app.repositories.slate_repository import SlateRepository
from app.services.active_slate_scope import build_active_slate_scope
from app.services.money_mode_service import build_money_mode
from app.services.money_mode_validation_service import validate_slate_for_money_mode
from app.services.slate_service import SlateService
from app.services.ticket_canary_dry_run_service import build_ticket_canary_dry_run

# The ten tracked tables whose counts must never move during a read-only run.
_COUNT_TABLES: dict[str, Any] = {
    "match_results": MatchResultModel,
    "predictions": PredictionModel,
    "matches": MatchModel,
    "progol_slate_matches": ProgolSlateMatchModel,
    "match_feature_snapshots": MatchFeatureSnapshotModel,
    "ticket_recommendation_snapshots": TicketRecommendationSnapshotModel,
    "team_rating_runs": TeamRatingRunModel,
    "team_rating_snapshots": TeamRatingSnapshotModel,
    "model_training_runs": ModelTrainingRunModel,
    "progol_slates": ProgolSlateModel,
}

_PLAYABLE_STATUSES = frozenset(
    {
        "JUGAR_BALANCEADO",
        "JUGAR_SOLO_BALANCEADO",
        "JUGAR_SOLO_CONSERVADOR",
        "JUGAR_SOLO_AGRESIVO",
        "JUGAR_CON_CAUTELA",
    }
)


def count_tracked_tables(session: Session) -> dict[str, int]:
    """Read-only count of the ten tracked tables."""
    return {
        name: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in _COUNT_TABLES.items()
    }


def _money_mode_ready(report: dict[str, Any]) -> bool:
    """A slate is Money-Mode-ready when validation has no hard blockers and a
    prediction source (persisted or live) is available."""
    validation = report.get("validation", {})
    return (
        not validation.get("data_blockers")
        and validation.get("prediction_status") in ("persisted", "live_available")
    )


def _slate_status(report: dict[str, Any]) -> dict[str, Any]:
    slate = report["slate"]
    decision = report["decision"]
    validation = report.get("validation", {})
    return {
        "draw_code": slate["draw_code"],
        "slate_id": slate["slate_id"],
        "week_type": slate["week_type"],
        "match_count": slate["match_count"],
        "decision": decision["status"],
        "reason": decision["reason"],
        "confidence": decision["confidence"],
        "recommended_ticket": decision["recommended_ticket"],
        "prediction_status": validation.get("prediction_status"),
        "data_blockers": validation.get("data_blockers", []),
        "warnings": validation.get("warnings", []),
        "do_not_simple_positions": report.get("do_not_simple_positions", []),
        "must_review_positions": report.get("must_review_positions", []),
        "money_mode_ready": _money_mode_ready(report),
        "playable": decision["status"] in _PLAYABLE_STATUSES,
    }


def build_operational_status(session: Session) -> dict[str, Any]:
    """Compact operational status for every active/upcoming slate (read-only)."""
    scope = build_active_slate_scope(session)
    slate_service = SlateService(SlateRepository(session))
    statuses: list[dict[str, Any]] = []
    playable = 0
    for info in scope:
        slate = slate_service.get_slate(info.slate_id)
        if slate is None:
            continue
        report = build_money_mode(session, slate)
        status = _slate_status(report)
        if status["playable"]:
            playable += 1
        statuses.append(status)
    return {
        "mode": "money_mode_operational_status",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "active_upcoming",
        "active_slate_count": len(statuses),
        "playable_slate_count": playable,
        "blocked_slate_count": len(statuses) - playable,
        "slates": statuses,
        "write_safety": {"read_only": True, "writes_performed": False, "snapshots_created": False},
    }


def run_operational_money_mode(
    session: Session,
    *,
    draw_code: str | None = None,
    slate_id: str | None = None,
    active_upcoming: bool = False,
) -> dict[str, Any]:
    """Full operational orchestration for the CLI (read-only).

    Order: active_slate_scope -> money_mode_validation -> ticket_canary_dry_run
    -> money_mode -> write-safety audit -> counts before/after.
    """
    slate_service = SlateService(SlateRepository(session))
    repo = SlateRepository(session)

    # Resolve the target slate(s).
    targets: list[ProgolSlateModel] = []
    if active_upcoming:
        scope = build_active_slate_scope(session)
        for info in scope:
            slate = slate_service.get_slate(info.slate_id)
            if slate is not None:
                targets.append(slate)
    else:
        slate = repo.get_slate(slate_id) if slate_id else (
            repo.find_by_draw_code(draw_code) if draw_code else None
        )
        if slate is None:
            return {
                "mode": "money_mode_operational_run",
                "error": "slate not found for the requested scope",
                "slates": [],
            }
        targets.append(slate)

    counts_before = count_tracked_tables(session)

    slates_out: list[dict[str, Any]] = []
    write_safety_ok = True
    playable = 0
    for slate in targets:
        validation = validate_slate_for_money_mode(session, slate)
        dry_run = build_ticket_canary_dry_run(session, slate)
        money_mode = build_money_mode(session, slate)
        ws = money_mode.get("write_safety", {})
        ws_dry = dry_run.get("write_safety", {})
        slate_ws_ok = (
            ws.get("writes_performed") is False
            and ws.get("snapshots_created") is False
            and ws_dry.get("writes_performed") is False
        )
        write_safety_ok = write_safety_ok and slate_ws_ok
        if money_mode["decision"]["status"] in _PLAYABLE_STATUSES:
            playable += 1
        slates_out.append(
            {
                "validation": validation,
                "ticket_canary_dry_run": dry_run,
                "money_mode": money_mode,
                "status": _slate_status(money_mode),
                "write_safety_ok": slate_ws_ok,
            }
        )

    counts_after = count_tracked_tables(session)
    counts_delta = {
        name: counts_after[name] - counts_before[name] for name in counts_before
    }
    delta_zero = all(value == 0 for value in counts_delta.values())

    return {
        "mode": "money_mode_operational_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "active_upcoming" if active_upcoming else "single_slate",
        "production_active": False,
        "ticket_integration_active": False,
        "optimizer_active": False,
        "slate_count": len(slates_out),
        "playable_slate_count": playable,
        "blocked_slate_count": len(slates_out) - playable,
        "slates": slates_out,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "counts_delta": counts_delta,
        "counts_delta_zero": delta_zero,
        "write_safety": {
            "read_only": True,
            "writes_performed": False,
            "snapshots_created": False,
            "audit_passed": write_safety_ok and delta_zero,
        },
    }
