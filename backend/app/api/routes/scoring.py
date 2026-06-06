from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.scoring import (
    ConfidenceBandScoreResponse,
    JornadaScoreResponse,
    MatchScoreDetailResponse,
)
from app.services.jornada_scoring_service import JornadaScoringService
from app.services.slate_service import SlateService

router = APIRouter(prefix="/scoring", tags=["scoring"])


def _to_response(score, *, snapshot_available: bool) -> JornadaScoreResponse:
    try:
        raw_details = json.loads(score.details_json or "[]")
    except (json.JSONDecodeError, TypeError):
        raw_details = []

    details = [MatchScoreDetailResponse(**d) for d in raw_details]

    confidence_bands = [
        ConfidenceBandScoreResponse(
            band="high",
            hits=score.high_confidence_hits,
            total=score.high_confidence_total,
            hit_rate=(
                round(score.high_confidence_hits / score.high_confidence_total, 4)
                if score.high_confidence_total > 0
                else None
            ),
        ),
        ConfidenceBandScoreResponse(
            band="medium",
            hits=score.medium_confidence_hits,
            total=score.medium_confidence_total,
            hit_rate=(
                round(score.medium_confidence_hits / score.medium_confidence_total, 4)
                if score.medium_confidence_total > 0
                else None
            ),
        ),
        ConfidenceBandScoreResponse(
            band="low",
            hits=score.low_confidence_hits,
            total=score.low_confidence_total,
            hit_rate=(
                round(score.low_confidence_hits / score.low_confidence_total, 4)
                if score.low_confidence_total > 0
                else None
            ),
        ),
        ConfidenceBandScoreResponse(
            band="blocked",
            hits=score.blocked_hits,
            total=score.blocked_total,
            hit_rate=(
                round(score.blocked_hits / score.blocked_total, 4)
                if score.blocked_total > 0
                else None
            ),
        ),
    ]

    return JornadaScoreResponse(
        id=score.id,
        slate_id=score.slate_id,
        draw_code=score.draw_code,
        week_type=score.week_type,
        composition_hash=score.composition_hash,
        slate_version=score.slate_version,
        total_matches=score.total_matches,
        matches_with_results=score.matches_with_results,
        simple_hits=score.simple_hits,
        simple_hit_rate=score.simple_hit_rate,
        ticket_hits=score.ticket_hits,
        ticket_hit_rate=score.ticket_hit_rate,
        brier_score_avg=score.brier_score_avg,
        confidence_bands=confidence_bands,
        details=details,
        computed_at=score.computed_at,
        is_complete=score.is_complete,
        snapshot_available=snapshot_available,
    )


@router.post(
    "/slates/{slate_id}/compute",
    response_model=JornadaScoreResponse,
    status_code=200,
    summary="Compute (or recompute) the jornada score for a slate",
)
async def compute_slate_score(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> JornadaScoreResponse:
    """Score the jornada: compare predictions to actual results.

    Safe to call repeatedly — each call updates the same row (keyed by
    slate_id + composition_hash) so partial scores can be refreshed as
    results arrive. Returns 422 if the slate has no composition_hash.
    """
    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    if not slate.composition_hash:
        raise HTTPException(
            status_code=422,
            detail="Slate has no composition_hash — run the backfill or upsert the slate first.",
        )

    scoring_svc = JornadaScoringService(session)
    score = scoring_svc.compute_for_slate(slate)
    snapshot_available = score.ticket_hits is not None

    repo = JornadaScoreRepository(session)
    saved = repo.upsert_score(score)
    session.commit()

    return _to_response(saved, snapshot_available=snapshot_available)


@router.get(
    "/slates/{slate_id}",
    response_model=JornadaScoreResponse,
    summary="Get the latest jornada score for a slate",
)
async def get_slate_score(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> JornadaScoreResponse:
    repo = JornadaScoreRepository(session)
    score = repo.get_latest_for_slate(slate_id)
    if score is None:
        raise HTTPException(
            status_code=404,
            detail="No score found for this slate. Call POST /compute first.",
        )
    snapshot_available = score.ticket_hits is not None
    return _to_response(score, snapshot_available=snapshot_available)


@router.get(
    "/history",
    response_model=list[JornadaScoreResponse],
    summary="List recent jornada scores across all slates",
)
async def list_scoring_history(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> list[JornadaScoreResponse]:
    repo = JornadaScoreRepository(session)
    scores = repo.list_history(limit=limit)
    return [_to_response(s, snapshot_available=s.ticket_hits is not None) for s in scores]
