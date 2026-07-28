import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.models.tables import EvidenceItemModel
from app.models.tables import MatchModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import SourceDocumentModel
from app.repositories.evidence_dedupe import dedupe_evidence_items
from app.repositories.evidence_dedupe import evidence_identity
from app.repositories.evidence_dedupe import evidence_identity_from_values

logger = logging.getLogger(__name__)


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

    def _record_stage_from_document(
        self,
        document: SourceDocumentModel,
        match_id: str,
    ) -> None:
        """Carry the fixture feed's reported stage onto the match.

        Linking a document is the first point where a stage from the
        feed meets the match it belongs to. Only fills a NULL stage —
        a value already recorded is never overwritten, so a late or
        lower-quality document cannot rewrite the phase of a fixture
        that was already resolved. Silent on any malformed payload:
        this is opportunistic enrichment and must never break linking.
        """
        try:
            payload = json.loads(document.payload_json or "{}")
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        fixtures = payload.get("fixtures")
        if not isinstance(fixtures, list):
            return
        stage = next(
            (
                str(fixture["stage"]).strip()[:64]
                for fixture in fixtures
                if isinstance(fixture, dict) and str(fixture.get("stage") or "").strip()
            ),
            None,
        )
        if not stage:
            return
        match = self.session.get(MatchModel, match_id)
        if match is None or match.stage:
            return
        match.stage = stage
        self.session.add(match)
        self._apply_stage_to_slate_links(match, stage)

    def _apply_stage_to_slate_links(self, match: MatchModel, stage: str) -> None:
        """Revise auto-set knockout flags now that the stage is known.

        The fixture feed almost always reports the stage after the slate
        has been built from the Progol guide, so the flag computed at
        build time was working from the competition name alone. Rows a
        human ruled on ('operator') are left exactly as they are.
        """
        # Imported here, as the slates route does: prediction_service
        # reaches back into the repositories package.
        from app.services.knockout_detection import detect_knockout_from_stage
        from app.services.prediction_service import invalidate_slate_prediction_cache

        resolved = detect_knockout_from_stage(stage)
        if resolved is None:
            return
        links = self.session.scalars(
            select(ProgolSlateMatchModel).where(
                ProgolSlateMatchModel.match_id == match.id,
                ProgolSlateMatchModel.knockout_source == "auto",
            )
        ).all()
        for link in links:
            if bool(link.is_knockout) == resolved:
                continue
            link.is_knockout = resolved
            self.session.add(link)
            logger.info(
                "slate_knockout_flag_revised_from_stage",
                extra={
                    "event": "slate_knockout_flag_revised_from_stage",
                    "slate_id": link.slate_id,
                    "position": link.position,
                    "match_id": match.id,
                    "stage": stage,
                    "is_knockout": resolved,
                },
            )
            invalidate_slate_prediction_cache(link.slate_id)

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
        self._record_stage_from_document(document, match_id)
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
