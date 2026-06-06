from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.result import MatchContextResultResponse
from app.schemas.result import MatchResultResponse
from app.services.result_service import ResultService

router = APIRouter(prefix="/results", tags=["results"])


def _serialize_context(repository: ResultRepository, current_match, item) -> MatchContextResultResponse:
    is_h2h = bool(current_match and repository.is_head_to_head(current_match, item.match))
    return MatchContextResultResponse(
        id=item.id,
        match_id=item.match_id,
        source_id=item.source_id,
        played_at=item.played_at,
        home_goals=item.home_goals,
        away_goals=item.away_goals,
        result_code=item.result_code,
        home_team_name=item.match.home_team.name,
        away_team_name=item.match.away_team.name,
        competition_name=item.match.competition.name,
        is_head_to_head=is_h2h,
        context_label="Antecedente directo" if is_h2h else "Forma reciente",
    )


@router.get("/matches/{match_id}", response_model=list[MatchResultResponse])
async def list_results_for_match(
    match_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchResultResponse]:
    service = ResultService(ResultRepository(session))
    return [MatchResultResponse.model_validate(item, from_attributes=True) for item in service.list_results_for_match(match_id)]


@router.get("/matches/{match_id}/context", response_model=list[MatchContextResultResponse])
async def list_context_results_for_match(
    match_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchContextResultResponse]:
    repository = ResultRepository(session)
    service = ResultService(repository)
    current_match = repository.get_match(match_id)
    return [
        _serialize_context(repository, current_match, item)
        for item in service.list_context_results_for_match(match_id)
    ]


@router.get("/slates/{slate_id}/context", response_model=dict[str, list[MatchContextResultResponse]])
async def list_slate_context(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, list[MatchContextResultResponse]]:
    """Batch context results for every match in the slate. Replaces N
    per-match round-trips with one — typical weekend slate goes from 14
    requests to 1."""
    slate = SlateRepository(session).get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    repository = ResultRepository(session)
    service = ResultService(repository)
    result: dict[str, list[MatchContextResultResponse]] = {}
    for link in slate.matches:
        if link.match_id is None:
            continue
        current_match = repository.get_match(link.match_id)
        result[link.match_id] = [
            _serialize_context(repository, current_match, item)
            for item in service.list_context_results_for_match(link.match_id)
        ]
    return result
