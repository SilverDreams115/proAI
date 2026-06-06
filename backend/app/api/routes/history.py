from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.ingestion_repository import IngestionRepository
from app.schemas.ingestion import IngestionRunResponse
from app.services.history_import_service import HistoryImportService

router = APIRouter(prefix="/history", tags=["history"])


@router.post("/sources/{source_id}/import", response_model=IngestionRunResponse, status_code=201)
async def import_source_history(
    source_id: str,
    session: Session = Depends(get_db_session),
) -> IngestionRunResponse:
    service = HistoryImportService(IngestionRepository(session))
    run = service.import_source_history(source_id)
    return IngestionRunResponse.model_validate(run, from_attributes=True)
