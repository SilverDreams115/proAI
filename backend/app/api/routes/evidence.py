import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.evidence import EvidenceResponse
from app.services.evidence_service import EvidenceService

router = APIRouter(prefix="/evidence", tags=["evidence"])


def _serialize(item) -> EvidenceResponse:
    payload = _evidence_payload(item.payload_json)
    return EvidenceResponse(
        id=item.id,
        match_id=item.match_id,
        source_id=item.source_id,
        kind=item.kind,
        captured_at=item.captured_at,
        confidence=item.confidence,
        summary=item.summary,
        source_title=str(payload.get("source_title") or payload.get("title") or "") or None,
        source_url=str(payload.get("source_url") or "") or None,
        context_summary=str(payload.get("context_summary") or payload.get("summary") or "") or None,
    )


@router.get("/matches/{match_id}", response_model=list[EvidenceResponse])
async def list_match_evidence(match_id: str, session: Session = Depends(get_db_session)) -> list[EvidenceResponse]:
    service = EvidenceService(EvidenceRepository(session))
    return [_serialize(item) for item in service.list_match_evidence(match_id)]


@router.get("/slates/{slate_id}", response_model=dict[str, list[EvidenceResponse]])
async def list_slate_evidence(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, list[EvidenceResponse]]:
    """Batch endpoint: returns evidence keyed by match_id for every match
    in the slate. The frontend would otherwise issue one request per
    match, multiplying network latency by the slate size (14 partidos on
    weekends). One round-trip beats fourteen."""
    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    service = EvidenceService(EvidenceRepository(session))
    result: dict[str, list[EvidenceResponse]] = {}
    for link in slate.matches:
        if link.match_id is None:
            continue
        result[link.match_id] = [_serialize(item) for item in service.list_match_evidence(link.match_id)]
    return result


def _evidence_payload(payload_json: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
