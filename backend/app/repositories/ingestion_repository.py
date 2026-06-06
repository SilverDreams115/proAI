import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from app.connectors.base import SourceDocument
from app.models.tables import IngestionRunModel
from app.models.tables import SourceDocumentModel
from app.models.tables import SourceModel


class IngestionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_source(self, source_id: str) -> SourceModel | None:
        statement = select(SourceModel).where(SourceModel.id == source_id)
        return self.session.scalar(statement)

    def list_runs(self) -> list[IngestionRunModel]:
        statement = select(IngestionRunModel).order_by(IngestionRunModel.started_at.desc())
        return list(self.session.scalars(statement))

    def create_run(self, source_id: str) -> IngestionRunModel:
        run = IngestionRunModel(source_id=source_id, status="running")
        self.session.add(run)
        self.session.flush()
        self.session.refresh(run)
        return run

    def mark_run_success(self, run: IngestionRunModel, documents: list[SourceDocument]) -> IngestionRunModel:
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.documents_found = len(documents)
        for document in documents:
            self.session.add(
                SourceDocumentModel(
                    ingestion_run_id=run.id,
                    source_id=run.source_id,
                    external_url=document.source_url,
                    title=str(document.payload.get("title", document.source_name)),
                    summary=str(document.payload.get("summary", "")),
                    payload_json=json.dumps(document.payload, sort_keys=True),
                    normalized_key=str(document.payload.get("normalized_key", "")),
                    captured_at=document.captured_at,
                )
            )
        self.session.add(run)
        self.session.flush()
        self.session.refresh(run)
        return run

    def mark_run_failure(self, run: IngestionRunModel, message: str) -> IngestionRunModel:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = message
        self.session.add(run)
        self.session.flush()
        self.session.refresh(run)
        return run

    def list_documents(
        self,
        source_ids: list[str] | None = None,
    ) -> list[SourceDocumentModel]:
        statement = select(SourceDocumentModel).options(joinedload(SourceDocumentModel.ingestion_run))
        if source_ids:
            statement = statement.where(SourceDocumentModel.source_id.in_(source_ids))
        statement = statement.order_by(SourceDocumentModel.captured_at.desc())
        return list(self.session.scalars(statement))
