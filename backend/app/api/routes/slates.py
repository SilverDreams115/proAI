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
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.schemas.slate import ActiveSlateResponse
from app.schemas.slate import DiscoveryInfo
from app.schemas.slate import ProgolSlateCreate
from app.schemas.slate import ProgolSlateResponse
from app.schemas.slate import PromoteProposalResponse
from app.schemas.slate import VisibleSlatesResponse
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
from app.services.operational_prediction_audit_service import OperationalPredictionAuditService
from app.services.slate_discovery_service import SlateDiscoveryService
from app.services.slate_proposal_service import SlateProposalService
from app.services.slate_readiness_report_service import build_slate_readiness_report
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
    # Official-lineage reality (demo/unverified excluded by callers). Computed
    # only when a session is available; classify_slate needs DB access.
    classification: str | None = None
    comparable = False
    has_results = False
    if session is not None:
        from app.services.slate_classification_service import classify_slate

        reality = classify_slate(session, slate)
        classification = reality.classification.value
        comparable = reality.comparable_with_results
        has_results = classification == "official_real"
    date_status = "date_valid"
    date_status_reasons: list[str] = []
    if session is not None:
        from app.services.date_sanity_service import slate_date_status

        status, date_status_reasons = slate_date_status(session, slate)
        date_status = status.value
    date_suspect = date_status != "date_valid"
    read_only = bool(slate.is_archived or is_closed)
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
        classification=classification,
        comparable=comparable,
        has_results=has_results,
        read_only=read_only,
        date_status=date_status,
        date_suspect=date_suspect,
        date_status_reasons=date_status_reasons,
    )


@router.get("", response_model=list[ProgolSlateResponse])
async def list_slates(
    include_closed: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> list[ProgolSlateResponse]:
    service = SlateService(SlateRepository(session))
    return [_serialize_slate(slate, service, session) for slate in service.list_slates(include_closed=include_closed)]


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


def _pdf_provenance(session: Session, slate: ProgolSlateModel) -> dict:
    """PDF source provenance + rejected-cierre-block info from the latest guide
    proposal for this slate's concurso (empty when none)."""
    import re

    m = re.search(r"(\d+)$", slate.draw_code or "")
    digits = m.group(1) if m else slate.draw_code
    proposal = session.scalar(
        select(ProgolSlateProposalModel)
        .where(
            ProgolSlateProposalModel.draw_code == digits,
            ProgolSlateProposalModel.source_name != "operator_date_override",
        )
        .order_by(ProgolSlateProposalModel.last_seen_at.desc())
        .limit(1)
    )
    if proposal is None:
        return {}
    try:
        payload = json.loads(proposal.payload_json or "{}")
    except (ValueError, TypeError):
        return {}
    block = payload.get("block_diagnostics") or {}
    return {
        "source_url": payload.get("source_url") or proposal.source_url,
        "pdf_sha256": payload.get("pdf_sha256"),
        "content_length": payload.get("content_length"),
        "fetched_at": payload.get("fetched_at"),
        "extracted_fixture_draw_code": block.get("fixture_draw_code") or payload.get("draw_code"),
        "match_count": payload.get("match_count"),
        "rejected_close_block_draw_code": block.get("rejected_close_block_draw_code"),
        "rejected_close_year": block.get("rejected_close_year"),
    }


def _discovery_info(
    session: Session, suspect_slates: list[dict] | None = None
) -> DiscoveryInfo:
    """Latest observed/promoted proposal per week_type — surfaced so the
    empty state explains discovery status instead of showing a blank UI."""

    def latest(week_type: str) -> ProgolSlateProposalModel | None:
        return session.scalar(
            select(ProgolSlateProposalModel)
            .where(ProgolSlateProposalModel.week_type == week_type)
            .order_by(ProgolSlateProposalModel.last_seen_at.desc())
            .limit(1)
        )

    weekend = latest("weekend")
    midweek = latest("midweek")
    last_observed = session.scalar(
        select(func.max(ProgolSlateProposalModel.last_seen_at))
    )
    # MS PDF watcher diagnostics + current MS candidate state.
    from app.services.ms_pdf_watch_service import latest_ms_pdf_watch_diagnostics

    watch = latest_ms_pdf_watch_diagnostics(session)
    ms_candidate = None
    ms_action = None
    if midweek is not None:
        ms_slate = session.scalar(
            select(ProgolSlateModel).where(
                ProgolSlateModel.draw_code.like(f"%{midweek.draw_code}"),
                ProgolSlateModel.week_type == "midweek",
            )
        )
        if ms_slate is not None:
            from app.services.date_sanity_service import slate_date_status

            ds, reasons = slate_date_status(session, ms_slate)
            ms_candidate = {
                "draw_code": ms_slate.draw_code,
                "date_status": ds.value,
                "activation_status": "open" if ds.value == "date_valid" and not ms_slate.is_archived else "blocked",
                "reason": reasons[0] if reasons else None,
            }
            ms_action = (
                "MS activada desde PDF oficial"
                if ds.value == "date_valid"
                else "Esperar PDF corregido de LN (cierre válido del concurso correcto)"
            )
    return DiscoveryInfo(
        last_weekend_draw_code=weekend.draw_code if weekend else None,
        last_weekend_status=weekend.status if weekend else None,
        last_weekend_seen_at=weekend.last_seen_at if weekend else None,
        last_midweek_draw_code=midweek.draw_code if midweek else None,
        last_midweek_status=midweek.status if midweek else None,
        last_midweek_seen_at=midweek.last_seen_at if midweek else None,
        last_observed_at=last_observed,
        suspect_slates=suspect_slates or [],
        last_ms_pdf_checked_at=watch.get("last_ms_pdf_checked_at"),
        last_ms_pdf_sha256=watch.get("last_ms_pdf_sha256"),
        last_ms_pdf_changed_at=watch.get("last_ms_pdf_changed_at"),
        last_ms_pdf_status=watch.get("last_ms_pdf_status"),
        current_ms_candidate=ms_candidate,
        ms_pdf_recommended_action=ms_action,
    )


@router.get("/visible", response_model=VisibleSlatesResponse)
async def visible_slates(
    limit_recent: int = Query(default=4, ge=1, le=12),
    session: Session = Depends(get_db_session),
) -> VisibleSlatesResponse:
    """Selector source of truth, never empty when official slates exist.

    Returns open official slates first; when none are open, falls back to the
    most recent official slates (read-only) that still have a prediction +
    valid snapshot so the postmortem is viewable. Demo/unverified slates are
    excluded via classify_slate. Weekend and Media Semana stay independent —
    callers group by ``week_type``; nothing is merged across types here.
    """
    from app.services.slate_classification_service import classify_slate
    from app.services.date_sanity import DateStatus
    from app.services.date_sanity_service import slate_date_status

    service = SlateService(SlateRepository(session))
    now = datetime.now(timezone.utc)
    # Official lineage only (comparable=True covers official_real and
    # official_but_no_results_yet); demo/unverified are dropped.
    official = [
        slate
        for slate in service.list_slates(include_closed=True)
        if classify_slate(session, slate).comparable_with_results
    ]
    # A slate may only be presented as OPEN when its dates pass the sanity gate.
    # Date-suspect / stale-source / needs-confirmation slates are held back
    # (shown in discovery diagnostics, never as a playable open boleta).
    def _date_ok(slate: ProgolSlateModel) -> bool:
        status, _ = slate_date_status(session, slate)
        return status == DateStatus.DATE_VALID

    open_slates = [
        s for s in official if not service.is_closed(s, now) and _date_ok(s)
    ]
    recent_closed = [
        s
        for s in official
        if service.is_closed(s, now) and _has_predictions_and_snapshot(session, s)
    ]
    def _closed_at(slate: ProgolSlateModel) -> datetime:
        when = slate.registration_closes_at or slate.created_at
        return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when

    # Open: soonest cierre first. Recent: most-recently closed first.
    open_slates.sort(key=lambda s: _closed_at(s) if s.registration_closes_at else datetime.max.replace(tzinfo=timezone.utc))
    recent_closed.sort(key=_closed_at, reverse=True)
    recent_closed = recent_closed[:limit_recent]

    # Diagnostics: official slates held back by the date gate, enriched with
    # PDF provenance so an operator sees the source bytes + the rejected block.
    # Archived slates are history, not a pending activation problem — they must
    # never resurface as "detectada, no jugable" (any concurso, not just PGM-803).
    suspect_slates: list[dict] = []
    for slate in official:
        if slate.is_archived:
            continue
        status, status_reasons = slate_date_status(session, slate)
        if status != DateStatus.DATE_VALID:
            entry = {
                "draw_code": slate.draw_code,
                "week_type": slate.week_type,
                "date_status": status.value,
                "activation_status": "blocked",
                "visible_as_open": False,
                "registration_closes_at": (
                    slate.registration_closes_at.isoformat()
                    if slate.registration_closes_at
                    else None
                ),
                "reasons": status_reasons,
                "recommended_action": (
                    "Esperar PDF corregido de LN o confirmar fecha oficial con evidencia."
                ),
            }
            entry.update(_pdf_provenance(session, slate))
            suspect_slates.append(entry)

    if open_slates:
        selected = open_slates[0].id
        reason = "open_slate"
    elif recent_closed:
        selected = recent_closed[0].id
        reason = "fallback_recent"
    else:
        selected = None
        reason = "no_official_slates"

    return VisibleSlatesResponse(
        open_slates=[_serialize_slate(s, service, session) for s in open_slates],
        recent_slates=[_serialize_slate(s, service, session) for s in recent_closed],
        selected_default_slate_id=selected,
        reason=reason,
        discovery=_discovery_info(session, suspect_slates),
    )


class DateOverrideRequest(BaseModel):
    # Operator-confirmed official cierre. Required — we never invent a date.
    registration_closes_at: datetime
    reason: str
    operator_note: str | None = None


@router.post("/{slate_id}/date-override", summary="Operator date override (traced)")
async def date_override(
    slate_id: str,
    body: DateOverrideRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    """Apply an operator-confirmed official cierre to a slate, fully traced.

    Used only when LN's guide is stale/ambiguous and the operator supplies the
    real date. Records old/new + source ``operator_date_override`` and emits a
    structured audit log. Never overwrites silently; a later LN ingest can
    still replace/confirm. Un-archives the slate so the gate can re-evaluate.
    """
    import logging

    slate = SlateService(SlateRepository(session)).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    old_closes = slate.registration_closes_at
    new_closes = body.registration_closes_at
    if new_closes.tzinfo is None:
        new_closes = new_closes.replace(tzinfo=timezone.utc)
    slate.registration_closes_at = new_closes
    # Let the date gate re-decide visibility; clear the immediate archive flag
    # so a valid future cierre can re-open the slate.
    if slate.is_archived and new_closes > datetime.now(timezone.utc):
        slate.is_archived = False
    session.add(slate)

    audit = {
        "event": "operator_date_override",
        "slate_id": slate.id,
        "draw_code": slate.draw_code,
        "source_name": "operator_date_override",
        "source_type": "operator_manual",
        "reason": body.reason,
        "operator_note": body.operator_note,
        "old_registration_closes_at": old_closes.isoformat() if old_closes else None,
        "new_registration_closes_at": new_closes.isoformat(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    # Persist a traceable proposal-style record (queryable + visible in debug).
    session.add(
        ProgolSlateProposalModel(
            draw_code=slate.draw_code,
            week_type=slate.week_type,
            source_name="operator_date_override",
            source_url="operator://date-override",
            status="operator_override",
            registration_closes_at=new_closes,
            payload_json=json.dumps(audit),
        )
    )
    logging.getLogger(__name__).info("operator_date_override", extra=audit)
    session.commit()
    from app.services.date_sanity_service import slate_date_status

    status, reasons = slate_date_status(session, slate)
    return {**audit, "date_status": status.value, "date_status_reasons": reasons}


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


@router.get("/{slate_id}/readiness-report")
async def get_slate_readiness_report(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return build_slate_readiness_report(session, include_archived=True, slate_ids={slate_id})


@router.get("/{slate_id}/publish-gate")
async def get_slate_publish_gate(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    audit = OperationalPredictionAuditService(session).build(slate_id=slate_id)
    return audit["publish_gate"]


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
