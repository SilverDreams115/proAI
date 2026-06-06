from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.slate_refresh import SlateAutoRefreshRequest
from app.schemas.slate_refresh import SlateAutoRefreshResponse
from app.services.ingestion_service import IngestionService
from app.services.slate_discovery_service import SlateDiscoveryService


class SlateRefreshService:
    def __init__(
        self,
        ingestion_repository: IngestionRepository,
        slate_repository: SlateRepository,
    ) -> None:
        self.ingestion_repository = ingestion_repository
        self.slate_repository = slate_repository

    def refresh(self, payload: SlateAutoRefreshRequest) -> SlateAutoRefreshResponse:
        source_ids: list[str] = []
        if payload.catalog_source_id:
            source_ids.append(payload.catalog_source_id)
        source_ids.extend(payload.fixture_source_ids)
        source_ids.extend(payload.availability_source_ids)

        ingested: list[str] = []
        ingestion_service = IngestionService(self.ingestion_repository)
        for source_id in dict.fromkeys(source_ids):
            run = ingestion_service.run_for_source(source_id)
            if run.status == "completed":
                ingested.append(source_id)

        discovery_request = payload.discovery.model_copy(
            update={
                "catalog_source_id": payload.discovery.catalog_source_id or payload.catalog_source_id,
                "fixture_source_ids": payload.discovery.fixture_source_ids or payload.fixture_source_ids,
            }
        )
        discovery = SlateDiscoveryService(
            self.ingestion_repository,
            self.slate_repository,
        ).discover(discovery_request)
        return SlateAutoRefreshResponse(ingested_source_ids=ingested, discovery=discovery)
