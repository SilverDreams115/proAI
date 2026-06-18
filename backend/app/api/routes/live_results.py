"""Live results tracking endpoints.

Exposes per-slate live/final results, partial/live scoring, and a small
dashboard that surfaces the most recent closed slates alongside the open
ones. Read-only: nothing here mutates predictions, snapshots, or marks a
slate complete — ``is_complete`` is derived purely from whether every
match has a FINAL canonical result.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.connectors.progol_resultados import ProgolResultadosConnector
from app.models.tables import (
    PredictionModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.slate_repository import SlateRepository
from app.schemas.live_results import (
    LiveDashboardEntry,
    LiveDashboardResponse,
    LiveResultsResponse,
    LiveScoreResponse,
    ResultComparisonResponse,
)
from app.schemas.tracking import TrackingResponse
from app.services.live_results_service import LiveResultsService
from app.services.results_ingestion_service import ResultsIngestionService
from app.services.slate_classification_service import classify_slate
from app.services.slate_service import SlateService
from app.services.tracking_service import TrackingService


class IngestResultsRequest(BaseModel):
    # Operator-pasted official LN results text. When omitted, the
    # endpoint fetches the LN results document from `source_url` (or the
    # connector default). Either path is real and traceable — nothing is
    # fabricated; pending matches simply stay pending.
    raw_text: str | None = None
    source_url: str | None = None

router = APIRouter(prefix="/slates", tags=["live-results"])

# How many closed / open slates the seguimiento dashboard surfaces.
DASHBOARD_LIMIT = 2


@router.get(
    "/live/dashboard",
    response_model=LiveDashboardResponse,
    summary="Seguimiento: 2 closed + 2 open slates with predictions",
)
async def live_dashboard(session: Session = Depends(get_db_session)) -> LiveDashboardResponse:
    service = SlateService(SlateRepository(session))
    now = datetime.now(timezone.utc)
    candidates = [
        slate
        for slate in service.list_slates(include_closed=True)
        if _has_predictions_and_snapshot(session, slate)
    ]
    closed = [s for s in candidates if service.is_closed(s, now)]
    open_ = [s for s in candidates if not service.is_closed(s, now)]
    # Closed: most recently closed first. Open: soonest to close first.
    closed.sort(key=lambda s: s.registration_closes_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    open_.sort(key=lambda s: s.registration_closes_at or datetime.max.replace(tzinfo=timezone.utc))

    live_service = LiveResultsService(session)
    return LiveDashboardResponse(
        closed=[_dashboard_entry(live_service, service, s, now) for s in closed[:DASHBOARD_LIMIT]],
        open=[_dashboard_entry(live_service, service, s, now) for s in open_[:DASHBOARD_LIMIT]],
    )


@router.get(
    "/{slate_id}/live-results",
    response_model=LiveResultsResponse,
    summary="Per-match prediction + live/final result + hit + draw coverage",
)
async def live_results(
    slate_id: str, session: Session = Depends(get_db_session)
) -> LiveResultsResponse:
    slate = _require_slate(session, slate_id)
    payload = LiveResultsService(session).build_live_results(slate)
    return LiveResultsResponse(**payload)


@router.get(
    "/{slate_id}/live-score",
    response_model=LiveScoreResponse,
    summary="Partial / live scoring (is_complete only when all matches final)",
)
async def live_score(
    slate_id: str, session: Session = Depends(get_db_session)
) -> LiveScoreResponse:
    slate = _require_slate(session, slate_id)
    payload = LiveResultsService(session).build_live_score(slate)
    return LiveScoreResponse(**payload)


@router.get(
    "/{slate_id}/result-comparison",
    response_model=ResultComparisonResponse,
    summary="Postmortem: original pre-close prediction vs real result, per match",
)
async def result_comparison(
    slate_id: str, session: Session = Depends(get_db_session)
) -> ResultComparisonResponse:
    slate = _require_slate(session, slate_id)
    payload = LiveResultsService(session).build_result_comparison(slate)
    return ResultComparisonResponse(**payload)


@router.get(
    "/{slate_id}/tracking",
    response_model=TrackingResponse,
    summary="Seguimiento: slate-level scoring + per-match prediction vs real result",
)
async def slate_tracking(
    slate_id: str, session: Session = Depends(get_db_session)
) -> TrackingResponse:
    """Phase A tracking view: original pick, raw/decision split, ticket
    strategy, real result, prediction_status (hit/miss/pending) and
    learning_status (ready/waiting_result/excluded) per match, plus a
    slate-level summary. Read-only; pending matches never become learning
    ready and conflicting results are excluded."""
    slate = _require_slate(session, slate_id)
    return TrackingResponse(**TrackingService(session).build_tracking(slate))


@router.get(
    "/{slate_id}/comparison",
    response_model=TrackingResponse,
    summary="Per-match comparison (alias of tracking, postmortem focus)",
)
async def slate_comparison(
    slate_id: str, session: Session = Depends(get_db_session)
) -> TrackingResponse:
    """Same enriched payload as ``/tracking`` — exposed under the name the
    Seguimiento UI uses for the per-match comparison table. Reuses
    TrackingService so the two endpoints never diverge."""
    slate = _require_slate(session, slate_id)
    return TrackingResponse(**TrackingService(session).build_tracking(slate))


@router.post(
    "/{slate_id}/ingest-results",
    summary="Ingest official Progol results (operator paste or LN fetch)",
)
async def ingest_results(
    slate_id: str,
    body: IngestResultsRequest | None = None,
    session: Session = Depends(get_db_session),
) -> dict:
    """Parse official results and feed them to LiveResultService.

    Source of truth is Lotería Nacional: either operator-pasted official
    text (`raw_text`) or a fetch of the LN results document. Mapping is by
    draw_code + casillero position; nothing is fabricated. Returns an
    ingestion report (recorded / finals / live / skipped_pending / etc.).
    """
    slate = _require_slate(session, slate_id)
    body = body or IngestResultsRequest()
    source_url = body.source_url
    text = body.raw_text
    if not text:
        connector = ProgolResultadosConnector(base_url=source_url) if source_url else ProgolResultadosConnector()
        try:
            documents = connector.fetch()
        except Exception as exc:  # network / parse failure — surface, don't crash
            raise HTTPException(status_code=502, detail=f"No se pudo obtener resultados LN: {exc}")
        text = str(documents[0].payload.get("raw_text", "")) if documents else ""
        source_url = source_url or connector.base_url
    report = ResultsIngestionService(session).ingest_for_slate(
        slate, text, source_url=source_url
    )
    session.commit()
    return report


def _require_slate(session: Session, slate_id: str) -> ProgolSlateModel:
    slate = SlateService(SlateRepository(session)).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return slate


def _has_predictions_and_snapshot(session: Session, slate: ProgolSlateModel) -> bool:
    has_pred = session.scalar(
        select(PredictionModel.id)
        .where(
            PredictionModel.slate_id == slate.id,
            PredictionModel.composition_hash == slate.composition_hash,
        )
        .limit(1)
    )
    has_snap = session.scalar(
        select(TicketRecommendationSnapshotModel.id)
        .where(
            TicketRecommendationSnapshotModel.slate_id == slate.id,
            TicketRecommendationSnapshotModel.is_valid.is_(True),
            TicketRecommendationSnapshotModel.composition_hash == slate.composition_hash,
        )
        .limit(1)
    )
    return has_pred is not None and has_snap is not None


def _dashboard_entry(
    live_service: LiveResultsService,
    slate_service: SlateService,
    slate: ProgolSlateModel,
    now: datetime,
) -> LiveDashboardEntry:
    score = live_service.build_live_score(slate)
    results = live_service.build_live_results(slate)
    is_closed = slate_service.is_closed(slate, now)
    reality = classify_slate(slate_service.repository.session, slate)
    return LiveDashboardEntry(
        slate_id=slate.id,
        draw_code=slate.draw_code,
        week_type=slate.week_type,
        is_archived=slate.is_archived,
        is_closed=is_closed,
        classification=reality.classification.value,
        comparable=reality.comparable_with_results,
        status_label=_status_label(slate, is_closed, results),
        match_count=score["total_matches"],
        completed_count=results["completed_count"],
        live_count=results["live_count"],
        pending_count=results["pending_count"],
        simple_hits=score["simple_hits"],
        doubles_hits=score["doubles_hits"],
        full_hits=score["full_hits"],
        empates_reales=score["empates_reales_hasta_ahora"],
        empates_esperados=score["empates_esperados"],
        max_possible_hits=score["max_possible_hits"],
        current_hit_rate=score["current_hit_rate"],
        is_complete=score["is_complete"],
        last_updated_at=score["last_updated_at"],
    )


def _status_label(slate: ProgolSlateModel, is_closed: bool, results: dict) -> str:
    if results["is_complete"]:
        return "Completa"
    if results["live_count"] > 0:
        return "En vivo"
    if slate.is_archived:
        return "Archivada"
    if is_closed:
        return "Cerrada"
    return "Abierta"
