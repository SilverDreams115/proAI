from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.source_repository import SourceRepository
from app.schemas.connector import ConnectorMetadataResponse
from app.schemas.provider import ProviderCatalogResponse
from app.schemas.provider_bootstrap import ProviderBootstrapRequest
from app.schemas.source import SourceCreate
from app.schemas.source import SourceResponse
from app.services.source_service import SourceService

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceResponse])
async def list_sources(session: Session = Depends(get_db_session)) -> list[SourceResponse]:
    service = SourceService(SourceRepository(session))
    return [SourceResponse.model_validate(source, from_attributes=True) for source in service.list_sources()]


@router.get("/connectors", response_model=list[ConnectorMetadataResponse])
async def list_registered_connectors(
    session: Session = Depends(get_db_session),
) -> list[ConnectorMetadataResponse]:
    service = SourceService(SourceRepository(session))
    return [
        ConnectorMetadataResponse.model_validate(metadata, from_attributes=True)
        for metadata in service.list_registered_connectors()
    ]


@router.get("/providers", response_model=list[ProviderCatalogResponse])
async def list_supported_providers(
    session: Session = Depends(get_db_session),
) -> list[ProviderCatalogResponse]:
    service = SourceService(SourceRepository(session))
    return [ProviderCatalogResponse.model_validate(item) for item in service.list_supported_providers()]


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    payload: SourceCreate,
    session: Session = Depends(get_db_session),
) -> SourceResponse:
    service = SourceService(SourceRepository(session))
    source = service.create_source(payload)
    return SourceResponse.model_validate(source, from_attributes=True)


@router.post("/providers/bootstrap", response_model=SourceResponse, status_code=201)
async def bootstrap_provider_source(
    payload: ProviderBootstrapRequest,
    session: Session = Depends(get_db_session),
) -> SourceResponse:
    service = SourceService(SourceRepository(session))
    source = service.create_source_from_provider(payload)
    return SourceResponse.model_validate(source, from_attributes=True)
