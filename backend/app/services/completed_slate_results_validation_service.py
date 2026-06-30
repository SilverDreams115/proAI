"""R6.4 — Completed-slate results validation (read-only dry-run).

For a finished slate (e.g. PG-2337, PGM-800) it compares, match by match, the
model's predicted sign against (a) any local ``match_results`` and (b) what the
free results provider reports — to decide whether tracking/learning can be
activated. It computes coverage, flags conflicts and missing results, and
proposes result signs. It **writes nothing**: applying results is a separate,
explicitly-confirmed CLI step (``apply_completed_slate_results.py``).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchResultModel, PredictionModel, ProgolSlateModel
from app.services.results_provider_service import build_slate_results_dry_run

_SIGN = {"1": "L", "X": "E", "2": "V"}


def _sign(code: str | None) -> str | None:
    if not code:
        return None
    return _SIGN.get(str(code), str(code))


def _latest_prediction_sign(session: Session, slate_id: str, match_id: str) -> str | None:
    # Latest prediction for this fixture. Scoped to the slate when its rows carry
    # slate_id, but falls back to any prediction for the match_id (some slates
    # persist predictions without slate_id), so a completed fixture is never
    # reported as prediction-less when a prediction exists.
    row = session.execute(
        select(PredictionModel.recommended_outcome)
        .where(PredictionModel.match_id == match_id)
        .order_by(
            (PredictionModel.slate_id == slate_id).desc(),
            PredictionModel.generated_at.desc(),
        )
        .limit(1)
    ).first()
    return _sign(row[0]) if row else None


def _local_result_sign(session: Session, match_id: str) -> str | None:
    row = session.execute(
        select(MatchResultModel.result_code)
        .where(MatchResultModel.match_id == match_id)
        .order_by(MatchResultModel.played_at.desc())
        .limit(1)
    ).first()
    return _sign(row[0]) if row else None


def _provider_sign(score: str | None, status: str | None) -> str | None:
    if status != "finished" or not score or "-" not in score:
        return None
    try:
        home, away = (int(part) for part in score.split("-", 1))
    except ValueError:
        return None
    if home > away:
        return "L"
    if home < away:
        return "V"
    return "E"


def build_completed_slate_validation(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Read-only result-validation dry-run for one (completed) slate."""
    provider = build_slate_results_dry_run(slate)
    provider_by_pos = {m["position"]: m for m in provider.get("matches", [])}

    rows: list[dict[str, Any]] = []
    predictions_count = 0
    local_results_count = 0
    provider_results_count = 0
    conflicts = 0
    covered = 0

    for link in sorted(slate.matches, key=lambda item: item.position):
        match = link.match
        home = getattr(match.home_team, "name", None)
        away = getattr(match.away_team, "name", None)
        prediction = _latest_prediction_sign(session, slate.id, match.id)
        local = _local_result_sign(session, match.id)
        pv = provider_by_pos.get(link.position, {})
        provider_sign = _provider_sign(pv.get("score"), pv.get("status"))

        if prediction is not None:
            predictions_count += 1
        if local is not None:
            local_results_count += 1
        if provider_sign is not None:
            provider_results_count += 1

        # Canonical observed sign: prefer a local canonical result, else provider.
        observed = local or provider_sign
        if observed is not None:
            covered += 1

        status = "missing"
        if local is not None and provider_sign is not None and local != provider_sign:
            status = "conflict"
            conflicts += 1
        elif observed is not None:
            status = "resolved"

        rows.append(
            {
                "position": link.position,
                "match": f"{home} vs {away}",
                "prediction": prediction,
                "local_result": local,
                "provider_result": provider_sign,
                "proposed_sign": observed,
                "hit": (prediction is not None and observed is not None and prediction == observed),
                "status": status,
            }
        )

    total = len(slate.matches)
    coverage = round(covered / total, 4) if total else 0.0

    blockers: list[str] = []
    if provider_results_count == 0:
        blockers.append("missing_provider_results")
    if local_results_count == 0:
        blockers.append("missing_local_results")
    if conflicts > 0:
        blockers.append("result_conflicts")
    if covered < total:
        blockers.append("incomplete_coverage")
    if predictions_count < total:
        blockers.append("incomplete_predictions")

    ready_to_apply = (
        total > 0
        and covered == total
        and conflicts == 0
        and provider_results_count == total  # high-confidence external source for all
    )

    hits = sum(1 for r in rows if r["hit"])
    return {
        "mode": "completed_slate_results_validation",
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "week_type": slate.week_type,
        "is_archived": bool(slate.is_archived),
        "match_count": total,
        "predictions_count": predictions_count,
        "local_results_count": local_results_count,
        "provider_results_count": provider_results_count,
        "provider_status": provider.get("status"),
        "coverage": coverage,
        "conflicts": conflicts,
        "hits": hits,
        "ready_to_apply": ready_to_apply,
        "blockers": blockers,
        "matches": rows,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_completed_slates_validation(session: Session) -> dict[str, Any]:
    """Validate every completed (archived) slate that has predictions."""
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    service = SlateService(SlateRepository(session))
    slates = service.list_slates(include_closed=True)
    out: list[dict[str, Any]] = []
    for slate in slates:
        if not slate.is_archived:
            continue
        if not slate.matches:
            continue
        out.append(build_completed_slate_validation(session, slate))
    return {
        "mode": "completed_slate_results_validation_all",
        "slate_count": len(out),
        "ready_count": sum(1 for s in out if s["ready_to_apply"]),
        "slates": out,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_completed_slate_validation_for_draw_code(
    session: Session, draw_code: str
) -> dict[str, Any] | None:
    from app.repositories.slate_repository import SlateRepository

    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        return None
    return build_completed_slate_validation(session, slate)
