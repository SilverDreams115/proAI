import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.models.tables import SourceModel
from app.models.tables import TeamModel
from app.repositories.availability_repository import AvailabilityRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.availability import PlayerAvailabilityResponse
from app.services.availability_service import AvailabilityService

router = APIRouter(prefix="/availability", tags=["availability"])


def _serialize(session: Session, item) -> PlayerAvailabilityResponse:
    payload = _availability_payload(item.payload_json)
    team = session.get(TeamModel, item.team_id)
    source = session.get(SourceModel, item.source_id)
    return PlayerAvailabilityResponse(
        id=item.id,
        match_id=item.match_id,
        team_id=item.team_id,
        team_name=team.name if team else None,
        player_id=item.player_id,
        source_id=item.source_id,
        source_name=source.name if source else None,
        source_url=str(payload.get("source_url") or "") or None,
        evidence_id=item.evidence_id,
        captured_at=item.captured_at,
        status=item.status,
        category=item.category,
        player_name=item.player_name,
        detail=item.detail,
        confidence=item.confidence,
        impact_score=item.impact_score,
    )


@router.get("/matches/{match_id}", response_model=list[PlayerAvailabilityResponse])
async def list_match_availability(
    match_id: str,
    session: Session = Depends(get_db_session),
) -> list[PlayerAvailabilityResponse]:
    service = AvailabilityService(AvailabilityRepository(session))
    return [_serialize(session, item) for item in service.list_match_availability(match_id)]


@router.get("/slates/{slate_id}", response_model=dict[str, list[PlayerAvailabilityResponse]])
async def list_slate_availability(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, list[PlayerAvailabilityResponse]]:
    """Batch endpoint — one round-trip instead of one per match."""
    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    service = AvailabilityService(AvailabilityRepository(session))
    result: dict[str, list[PlayerAvailabilityResponse]] = {}
    for link in slate.matches:
        if link.match_id is None:
            continue
        result[link.match_id] = [
            _serialize(session, item) for item in service.list_match_availability(link.match_id)
        ]
    return result


def _availability_payload(payload_json: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
