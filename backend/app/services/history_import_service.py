from app.repositories.ingestion_repository import IngestionRepository
from app.services.ingestion_service import IngestionService


class HistoryImportService:
    def __init__(self, repository: IngestionRepository) -> None:
        self.repository = repository

    def import_source_history(self, source_id: str):
        return IngestionService(self.repository).run_for_source(source_id)
