from datetime import datetime

from pydantic import BaseModel


class IngestionRunCreate(BaseModel):
    source_id: str


class IngestionRunResponse(BaseModel):
    id: str
    source_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    documents_found: int
    error_message: str | None
