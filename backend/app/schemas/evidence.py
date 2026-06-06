from datetime import datetime

from pydantic import BaseModel


class EvidenceResponse(BaseModel):
    id: str
    match_id: str
    source_id: str
    kind: str
    captured_at: datetime
    confidence: float
    summary: str
    source_title: str | None = None
    source_url: str | None = None
    context_summary: str | None = None


class DocumentLinkResponse(BaseModel):
    document_id: str
    matched_match_id: str
    linked_evidence_id: str
