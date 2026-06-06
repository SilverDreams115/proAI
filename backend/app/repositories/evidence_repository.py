import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.models.tables import EvidenceItemModel
from app.models.tables import MatchModel
from app.models.tables import SourceDocumentModel
from app.repositories.evidence_dedupe import dedupe_evidence_items
from app.repositories.evidence_dedupe import evidence_identity
from app.repositories.evidence_dedupe import evidence_identity_from_values


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_match_evidence(self, match_id: str) -> list[EvidenceItemModel]:
        statement = (
            select(EvidenceItemModel)
            .where(EvidenceItemModel.match_id == match_id)
            .options(joinedload(EvidenceItemModel.source))
            .order_by(EvidenceItemModel.captured_at.desc())
        )
        return dedupe_evidence_items(list(self.session.scalars(statement)))

    def get_document(self, document_id: str) -> SourceDocumentModel | None:
        return self.session.get(SourceDocumentModel, document_id)

    def list_unlinked_documents(self) -> list[SourceDocumentModel]:
        statement = select(SourceDocumentModel).where(SourceDocumentModel.matched_match_id.is_(None))
        return list(self.session.scalars(statement))

    def get_match_with_relations(self, match_id: str) -> MatchModel | None:
        statement = (
            select(MatchModel)
            .where(MatchModel.id == match_id)
            .options(
                joinedload(MatchModel.home_team).joinedload("*"),
                joinedload(MatchModel.away_team).joinedload("*"),
                joinedload(MatchModel.competition).joinedload("*"),
            )
        )
        return self.session.scalar(statement)

    def create_evidence_for_document(
        self,
        document: SourceDocumentModel,
        match_id: str,
        summary: str,
        confidence: float,
        payload: dict[str, object],
    ) -> EvidenceItemModel:
        evidence_payload = {
            **payload,
            "source_title": document.title,
            "source_url": document.external_url,
        }
        expected_identity = evidence_identity_from_values(
            match_id=match_id,
            source_id=document.source_id,
            kind="news",
            summary=summary,
            payload=evidence_payload,
        )
        if document.linked_evidence_id:
            existing = self.session.get(EvidenceItemModel, document.linked_evidence_id)
            if existing is not None:
                existing.summary = summary
                existing.confidence = confidence
                existing.payload_json = json.dumps(evidence_payload, sort_keys=True)
                self.session.add(existing)
                self.session.flush()
                self.session.refresh(existing)
                return existing
        existing_statement = select(EvidenceItemModel).where(
            EvidenceItemModel.match_id == match_id,
            EvidenceItemModel.source_id == document.source_id,
            EvidenceItemModel.kind == "news",
        )
        for existing in self.session.scalars(existing_statement):
            if evidence_identity(existing) != expected_identity:
                continue
            existing.summary = summary
            existing.confidence = confidence
            existing.payload_json = json.dumps(evidence_payload, sort_keys=True)
            document.matched_match_id = match_id
            document.linked_evidence_id = existing.id
            self.session.add(existing)
            self.session.add(document)
            self.session.flush()
            self.session.refresh(existing)
            return existing
        evidence = EvidenceItemModel(
            match_id=match_id,
            source_id=document.source_id,
            kind="news",
            captured_at=document.captured_at,
            confidence=confidence,
            summary=summary,
            payload_json=json.dumps(evidence_payload, sort_keys=True),
        )
        self.session.add(evidence)
        self.session.flush()
        document.matched_match_id = match_id
        document.linked_evidence_id = evidence.id
        self.session.add(document)
        self.session.refresh(evidence)
        return evidence
