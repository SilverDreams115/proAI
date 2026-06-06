from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.stats_repository import StatsRepository
from app.schemas.stats import MatchStatResponse
from app.schemas.stats import TeamStatResponse
from app.services.stats_service import StatsService

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/teams/{team_id}", response_model=list[TeamStatResponse])
async def list_team_stats(team_id: str, session: Session = Depends(get_db_session)) -> list[TeamStatResponse]:
    service = StatsService(StatsRepository(session))
    return [TeamStatResponse.model_validate(item, from_attributes=True) for item in service.list_team_stats(team_id)]


@router.get("/matches/{match_id}", response_model=list[MatchStatResponse])
async def list_match_stats(match_id: str, session: Session = Depends(get_db_session)) -> list[MatchStatResponse]:
    service = StatsService(StatsRepository(session))
    return [MatchStatResponse.model_validate(item, from_attributes=True) for item in service.list_match_stats(match_id)]
