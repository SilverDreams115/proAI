from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.scheduler_repository import SchedulerRepository
from app.schemas.ingestion import IngestionRunResponse
from app.schemas.scheduler import ScheduledJobCreate
from app.schemas.scheduler import ScheduledJobResponse
from app.schemas.scheduler import SourceHealthResponse
from app.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("/jobs", response_model=list[ScheduledJobResponse])
async def list_jobs(session: Session = Depends(get_db_session)) -> list[ScheduledJobResponse]:
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    return [ScheduledJobResponse.model_validate(job, from_attributes=True) for job in service.list_jobs()]


@router.post("/jobs", response_model=ScheduledJobResponse, status_code=201)
async def create_job(
    payload: ScheduledJobCreate,
    session: Session = Depends(get_db_session),
) -> ScheduledJobResponse:
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    job = service.create_job(payload)
    return ScheduledJobResponse.model_validate(job, from_attributes=True)


@router.post("/jobs/run-due", response_model=list[IngestionRunResponse])
async def run_due_jobs(session: Session = Depends(get_db_session)) -> list[IngestionRunResponse]:
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    runs = service.run_due_jobs()
    return [IngestionRunResponse.model_validate(run, from_attributes=True) for run in runs]


@router.post("/sources/{source_id}/health", response_model=SourceHealthResponse, status_code=201)
async def check_source_health(source_id: str, session: Session = Depends(get_db_session)) -> SourceHealthResponse:
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    check = service.record_source_health(source_id)
    return SourceHealthResponse.model_validate(check, from_attributes=True)


@router.get("/sources/health", response_model=list[SourceHealthResponse])
async def list_source_health_checks(session: Session = Depends(get_db_session)) -> list[SourceHealthResponse]:
    service = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    return [SourceHealthResponse.model_validate(item, from_attributes=True) for item in service.list_health_checks()]
