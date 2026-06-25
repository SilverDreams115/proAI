"""R7.0 — Completed-slate learning inventory (read-only).

Classifies every Progol slate by its *learning state* so the post-jornada
learning loop knows which slates are comparable (predictions vs official
results) and which are blocked, and why. This is the entry point of the
completed-slate learning loop: nothing downstream (scoring, error
attribution, dataset readiness) should treat a slate as learnable unless
this inventory marks it ``comparable``.

It is strictly read-only — it reuses the results-validation dry-run and the
canonical-result repository, and writes nothing.

Inventory states
----------------
``active``                    not archived, registration still open-ended/now.
``upcoming``                  not archived, registration closes in the future.
``closed_pending_results``    archived, has predictions, zero canonical results.
``closed_partial_results``    archived, has predictions, partial canonical results.
``closed_comparable``         archived, full canonical results, no conflicts,
                              official lineage, full prediction coverage.
``closed_conflict``           archived with conflicting result codes across sources.
``archived_no_predictions``   archived with no predictions to learn from.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel, ProgolSlateModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.services.completed_slate_results_validation_service import (
    build_completed_slate_validation,
)
from app.services.slate_classification_service import classify_slate

ACTIVE = "active"
UPCOMING = "upcoming"
CLOSED_PENDING_RESULTS = "closed_pending_results"
CLOSED_PARTIAL_RESULTS = "closed_partial_results"
CLOSED_COMPARABLE = "closed_comparable"
CLOSED_CONFLICT = "closed_conflict"
ARCHIVED_NO_PREDICTIONS = "archived_no_predictions"


def _prediction_match_count(session: Session, slate: ProgolSlateModel) -> int:
    """How many of the slate's fixtures have at least one prediction.

    Mirrors the validation service's lenient lookup: a prediction counts even
    when it was persisted without a slate_id, so a completed fixture is never
    reported as prediction-less when a prediction exists.
    """
    match_ids = [sm.match_id for sm in slate.matches]
    if not match_ids:
        return 0
    rows = session.execute(
        select(PredictionModel.match_id)
        .where(PredictionModel.match_id.in_(match_ids))
        .distinct()
    ).all()
    return len(rows)


def _canonical_counts(session: Session, slate: ProgolSlateModel) -> tuple[int, int]:
    """Return (canonical_result_count, conflict_count) using cross-source rules.

    A match whose result codes disagree across sources is a conflict and is
    excluded from the canonical count.
    """
    match_ids = [sm.match_id for sm in slate.matches]
    info = CanonicalResultRepository(session).get_with_conflict_info(match_ids)
    canonical = sum(1 for cr in info.values() if not cr.is_conflicting)
    conflicts = sum(1 for cr in info.values() if cr.is_conflicting)
    return canonical, conflicts


def _inventory_state(
    slate: ProgolSlateModel,
    *,
    match_count: int,
    prediction_count: int,
    canonical_count: int,
    conflicts: int,
    comparable_lineage: bool,
    now: datetime,
) -> str:
    if not slate.is_archived:
        closes = slate.registration_closes_at
        if closes is not None and closes.tzinfo is None:
            closes = closes.replace(tzinfo=timezone.utc)
        if closes is not None and closes > now:
            return UPCOMING
        return ACTIVE
    # Archived / closed slates from here down.
    if prediction_count == 0:
        return ARCHIVED_NO_PREDICTIONS
    if conflicts > 0:
        return CLOSED_CONFLICT
    if canonical_count == 0:
        return CLOSED_PENDING_RESULTS
    if canonical_count < match_count:
        return CLOSED_PARTIAL_RESULTS
    # Full canonical coverage, no conflicts.
    if comparable_lineage and prediction_count >= match_count:
        return CLOSED_COMPARABLE
    return CLOSED_PARTIAL_RESULTS


def build_slate_inventory_item(
    session: Session, slate: ProgolSlateModel
) -> dict[str, Any]:
    """Read-only learning-inventory record for one slate."""
    validation = build_completed_slate_validation(session, slate)
    reality = classify_slate(session, slate)
    canonical_count, conflicts = _canonical_counts(session, slate)
    prediction_count = _prediction_match_count(session, slate)
    match_count = len(slate.matches)
    now = datetime.now(timezone.utc)

    state = _inventory_state(
        slate,
        match_count=match_count,
        prediction_count=prediction_count,
        canonical_count=canonical_count,
        conflicts=conflicts,
        comparable_lineage=reality.comparable_with_results,
        now=now,
    )
    comparable = state == CLOSED_COMPARABLE

    blockers = list(validation["blockers"])
    if conflicts > 0:
        blockers.append("result_conflict")
    if not reality.comparable_with_results and slate.is_archived:
        blockers.append("not_official_comparable_lineage")
    if canonical_count < match_count:
        blockers.append("incomplete_canonical_results")
    # De-duplicate while preserving order.
    blockers = list(dict.fromkeys(blockers))

    return {
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "week_type": slate.week_type,
        "is_archived": bool(slate.is_archived),
        "state": state,
        "comparable": comparable,
        "match_count": match_count,
        "prediction_count": prediction_count,
        "local_result_count": int(validation["local_results_count"]),
        "provider_result_count": int(validation["provider_results_count"]),
        "canonical_result_count": canonical_count,
        "conflicts": conflicts,
        "coverage": validation["coverage"],
        "classification": reality.classification.value,
        "comparable_lineage": reality.comparable_with_results,
        "blockers": blockers if not comparable else [],
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_completed_slate_inventory(session: Session) -> dict[str, Any]:
    """Read-only learning inventory for every slate.

    Slates are ordered with comparable/closed ones first so the post-jornada
    consumer sees actionable slates at the top.
    """
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    service = SlateService(SlateRepository(session))
    slates = service.list_slates(include_closed=True)
    items = [build_slate_inventory_item(session, slate) for slate in slates]
    items.sort(key=lambda it: (not it["comparable"], it["draw_code"]))

    by_state: dict[str, int] = {}
    for it in items:
        by_state[it["state"]] = by_state.get(it["state"], 0) + 1

    return {
        "mode": "completed_slate_learning_inventory",
        "slate_count": len(items),
        "comparable_count": sum(1 for it in items if it["comparable"]),
        "by_state": by_state,
        "slates": items,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_slate_inventory_for_draw_code(
    session: Session, draw_code: str
) -> dict[str, Any] | None:
    from app.repositories.slate_repository import SlateRepository

    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        return None
    return build_slate_inventory_item(session, slate)
