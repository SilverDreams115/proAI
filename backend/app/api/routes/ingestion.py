from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.ingestion_repository import IngestionRepository
from app.schemas.ingestion import IngestionRunCreate
from app.schemas.ingestion import IngestionRunResponse
from app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.get("/runs", response_model=list[IngestionRunResponse])
async def list_ingestion_runs(session: Session = Depends(get_db_session)) -> list[IngestionRunResponse]:
    service = IngestionService(IngestionRepository(session))
    return [IngestionRunResponse.model_validate(run, from_attributes=True) for run in service.list_runs()]


@router.post("/runs", response_model=IngestionRunResponse, status_code=201)
async def create_ingestion_run(
    payload: IngestionRunCreate,
    session: Session = Depends(get_db_session),
) -> IngestionRunResponse:
    service = IngestionService(IngestionRepository(session))
    run = service.run_for_source(payload.source_id)
    return IngestionRunResponse.model_validate(run, from_attributes=True)
