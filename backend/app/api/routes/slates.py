import json
from datetime import datetime, timezone

from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateModel
from app.models.tables import ProgolSlateProposalModel
from app.models.tables import TicketRecommendationSnapshotModel
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.schemas.slate import ActiveSlateResponse
from app.schemas.slate import ProgolSlateCreate
from app.schemas.slate import ProgolSlateResponse
from app.schemas.slate import PromoteProposalResponse
from app.schemas.slate import SlateMatchResponse
from app.schemas.slate import SlateProposalFixture
from app.schemas.slate import SlateProposalResponse
from app.schemas.slate_refresh import CurrentProgolRefreshRequest
from app.schemas.slate_refresh import CurrentProgolRefreshResponse
from app.schemas.slate_discovery import SlateDiscoveryRequest
from app.schemas.slate_discovery import SlateDiscoveryResponse
from app.schemas.slate_refresh import SlateAutoRefreshRequest
from app.schemas.slate_refresh import SlateAutoRefreshResponse
from app.services.current_progol_service import CurrentProgolService
from app.services.slate_discovery_service import SlateDiscoveryService
from app.services.slate_proposal_service import SlateProposalService
from app.services.slate_refresh_service import SlateRefreshService
from app.services.slate_service import SlateService

router = APIRouter(prefix="/slates", tags=["slates"])


def _serialize_slate(
    slate: ProgolSlateModel,
    service: SlateService,
    session: Session | None = None,
) -> ProgolSlateResponse:
    has_predictions = False
    has_valid_snapshot = False
    persisted_prediction_count = 0
    if session is not None:
        persisted_prediction_count = int(
            session.scalar(
                select(func.count(PredictionModel.id)).where(
                    PredictionModel.slate_id == slate.id
                )
            )
            or 0
        )
        has_predictions = persisted_prediction_count > 0
        has_valid_snapshot = session.scalar(
            select(TicketRecommendationSnapshotModel.id)
            .where(
                TicketRecommendationSnapshotModel.slate_id == slate.id,
                TicketRecommendationSnapshotModel.is_valid.is_(True),
                TicketRecommendationSnapshotModel.composition_hash == slate.composition_hash,
            )
            .limit(1)
        ) is not None
    is_closed = service.is_closed(slate)
    match_count = len(slate.matches)
    # Live predictions are computable read-only for any active slate that has
    # matches, even with zero persisted rows — the GET predictions endpoint
    # scores them on demand. So an active slate is never a true "Sin predicción".
    live_prediction_available = bool(match_count) and not slate.is_archived and not is_closed
    if has_predictions:
        prediction_status = "persisted"
    elif live_prediction_available:
        prediction_status = "live_available"
    elif match_count:
        prediction_status = "pending"
    else:
        prediction_status = "missing"
    if slate.is_archived:
        status_label = "Archivada"
    elif is_closed:
        status_label = "Cerrada"
    elif has_valid_snapshot:
        status_label = "Con ticket"
    elif has_predictions:
        status_label = "Con predicciones"
    elif live_prediction_available:
        status_label = "Predicción live"
    elif match_count:
        status_label = "Pendiente de predicción"
    else:
        status_label = "Sin datos"
    return ProgolSlateResponse(
        id=slate.id,
        label=slate.label,
        draw_code=slate.draw_code,
        week_type=slate.week_type,
        registration_closes_at=slate.registration_closes_at,
        is_archived=slate.is_archived,
        is_closed=is_closed,
        created_at=slate.created_at,
        matches=[
            SlateMatchResponse(
                position=slate_match.position,
                match_id=slate_match.match.id,
                competition_name=slate_match.match.competition.name,
                home_team_name=slate_match.match.home_team.name,
                away_team_name=slate_match.match.away_team.name,
                kickoff_at=slate_match.match.kickoff_at,
                venue=slate_match.match.venue,
            )
            for slate_match in sorted(slate.matches, key=lambda item: item.position)
        ],
        has_predictions=has_predictions,
        has_valid_snapshot=has_valid_snapshot,
        status_label=status_label,
        prediction_status=prediction_status,
        persisted_prediction_count=persisted_prediction_count,
        match_count=match_count,
        live_prediction_available=live_prediction_available,
    )


@router.get("", response_model=list[ProgolSlateResponse])
async def list_slates(
    include_closed: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> list[ProgolSlateResponse]:
    service = SlateService(SlateRepository(session))
    return [_serialize_slate(slate, service, session) for slate in service.list_slates(include_closed=include_closed)]


@router.get("/active", response_model=ActiveSlateResponse)
async def get_active_slate(
    session: Session = Depends(get_db_session),
) -> ActiveSlateResponse:
    # /slates/active is the entry point the frontend polls every minute.
    # When the active slate's cierre passes the worker archives it and a
    # subsequent call returns the next one, enabling auto-transition with
    # no client-side coupling to slate IDs. Always returns 200 with
    # `slate: null` when nothing is active so the frontend doesn't need
    # 204 handling.
    service = SlateService(SlateRepository(session))
    now = datetime.now(timezone.utc)
    slate = service.get_active_slate(now)
    if slate is None:
        return ActiveSlateResponse(slate=None, seconds_to_close=None, server_time=now)
    seconds_to_close: int | None = None
    if slate.registration_closes_at is not None:
        closes_at = slate.registration_closes_at
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        seconds_to_close = max(0, int((closes_at - now).total_seconds()))
    return ActiveSlateResponse(
        slate=_serialize_slate(slate, service, session),
        seconds_to_close=seconds_to_close,
        server_time=now,
    )


@router.post("", response_model=ProgolSlateResponse, status_code=201)
async def create_slate(
    payload: ProgolSlateCreate,
    session: Session = Depends(get_db_session),
) -> ProgolSlateResponse:
    service = SlateService(SlateRepository(session))
    slate = service.create_slate(payload)
    return _serialize_slate(slate, service, session)


@router.post("/{slate_id}/matches/{position}/knockout", status_code=204)
async def set_slate_match_knockout(
    slate_id: str,
    position: int,
    is_knockout: bool = Query(True),
    session: Session = Depends(get_db_session),
) -> None:
    """Mark (or clear) a slate position as a knockout / final fixture.

    When ``is_knockout=true`` the prediction service forces the
    recommendation to be L or V on that position — the boleta in
    elimination fixtures does not accept "X". Defaults to ``true`` so
    the simple call ``POST .../knockout`` flips the flag on.
    """
    from sqlalchemy import select

    from app.models.tables import ProgolSlateMatchModel
    from app.services.prediction_service import invalidate_slate_prediction_cache

    row = session.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate_id,
            ProgolSlateMatchModel.position == position,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Slate position not found.")
    row.is_knockout = bool(is_knockout)
    session.commit()
    invalidate_slate_prediction_cache(slate_id)


@router.post("/discover", response_model=SlateDiscoveryResponse, status_code=201)
async def discover_slate(
    payload: SlateDiscoveryRequest,
    session: Session = Depends(get_db_session),
) -> SlateDiscoveryResponse:
    service = SlateDiscoveryService(IngestionRepository(session), SlateRepository(session))
    return service.discover(payload)


@router.post("/refresh", response_model=SlateAutoRefreshResponse, status_code=201)
async def refresh_slate(
    payload: SlateAutoRefreshRequest,
    session: Session = Depends(get_db_session),
) -> SlateAutoRefreshResponse:
    service = SlateRefreshService(IngestionRepository(session), SlateRepository(session))
    return service.refresh(payload)


@router.post("/current/refresh", response_model=CurrentProgolRefreshResponse, status_code=201)
async def refresh_current_progol(
    payload: CurrentProgolRefreshRequest | None = None,
    session: Session = Depends(get_db_session),
) -> CurrentProgolRefreshResponse:
    request = payload or CurrentProgolRefreshRequest()
    service = CurrentProgolService(
        SourceRepository(session),
        IngestionRepository(session),
        SlateRepository(session),
    )
    return service.refresh_current(source_name=request.source_name, local_path=request.local_path)


def _serialize_proposal(
    proposal: ProgolSlateProposalModel,
    *,
    active_slate_id: str | None = None,
) -> SlateProposalResponse:
    try:
        payload = json.loads(proposal.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    fixtures = [
        SlateProposalFixture(
            position=int(fixture.get("position", 0)),
            home=str(fixture.get("home", "")),
            away=str(fixture.get("away", "")),
        )
        for fixture in payload.get("fixtures", [])
    ]
    return SlateProposalResponse(
        id=proposal.id,
        draw_code=proposal.draw_code,
        week_type=proposal.week_type,
        source_name=proposal.source_name,
        source_url=proposal.source_url,
        registration_closes_at=proposal.registration_closes_at,
        status=proposal.status,
        observations=proposal.observations,
        first_seen_at=proposal.first_seen_at,
        last_seen_at=proposal.last_seen_at,
        fixtures=fixtures,
        promoted_slate_id=proposal.promoted_slate_id,
        is_already_active=active_slate_id is not None,
        active_slate_id=active_slate_id,
    )


def _find_active_slate_id_for_proposal(
    proposal: ProgolSlateProposalModel,
    session,
) -> str | None:
    """Return the slate_id if a non-archived slate already exists for this
    proposal's draw_code, so the UI can surface 'Ya activa / Ver boleta'."""
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_proposal_service import _WEEK_TYPE_PREFIX

    prefix = _WEEK_TYPE_PREFIX.get(proposal.week_type, "PG")
    formatted_draw_code = f"{prefix}-{proposal.draw_code}"
    slate = SlateRepository(session).find_by_draw_code(formatted_draw_code)
    if slate is not None and not slate.is_archived:
        return slate.id
    return None


@router.get("/proposed", response_model=list[SlateProposalResponse])
async def list_proposed_slates(
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> list[SlateProposalResponse]:
    service = SlateProposalService(session)
    return [
        _serialize_proposal(
            item,
            active_slate_id=_find_active_slate_id_for_proposal(item, session),
        )
        for item in service.list_proposals(status=status)
    ]


@router.post("/proposed/observe", response_model=SlateProposalResponse | None)
async def trigger_proposal_observation(
    session: Session = Depends(get_db_session),
) -> SlateProposalResponse | None:
    """Operator-triggered manual fetch of the LN guide PDF. Mostly useful
    for ad-hoc debugging; the worker calls this on a 60-minute cadence
    automatically. Returns the proposal row (observed or validated)."""
    service = SlateProposalService(session)
    proposal = service.observe()
    if proposal is None:
        return None
    return _serialize_proposal(proposal)


@router.post("/proposed/{proposal_id}/promote", response_model=PromoteProposalResponse, status_code=200)
async def promote_proposed_slate(
    proposal_id: str,
    session: Session = Depends(get_db_session),
) -> PromoteProposalResponse:
    """Turn a validated proposal into a real slate.

    Returns already_active=True when a slate for this draw_code was
    already active with the same fixture composition — in that case the
    UI should show "Ya activa / Ver boleta" rather than treating it as a
    fresh creation.  already_active=False means a new (or updated) slate
    was created.

    Delegates fixture matching to SlateProposalService.promote_proposal
    which (1) tries to match each (home, away) pair against real
    upcoming matches in the DB and (2) falls back to a synthetic
    placeholder fixture for pairs we don't have data for. The worker
    auto-promote job uses the same code path.
    """
    proposal_service = SlateProposalService(session)
    proposal = proposal_service.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    try:
        result = proposal_service.promote_proposal(proposal, actor="operator")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    slate_service = SlateService(SlateRepository(session))
    return PromoteProposalResponse(
        already_active=result.already_active,
        slate=_serialize_slate(result.slate, slate_service, session),
    )


# Declared last so it doesn't shadow the more specific `/proposed`,
# `/active`, etc. routes. FastAPI matches in declaration order and a
# bare `/{slate_id}` is the most permissive segment in this router.
@router.get("/{slate_id}", response_model=ProgolSlateResponse)
async def get_slate(slate_id: str, session: Session = Depends(get_db_session)) -> ProgolSlateResponse:
    service = SlateService(SlateRepository(session))
    slate = service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return _serialize_slate(slate, service, session)
