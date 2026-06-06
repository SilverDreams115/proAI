import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.models.tables import EvidenceItemModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PlayerAvailabilityModel
from app.models.tables import SourceDocumentModel
from app.repositories.evidence_dedupe import dedupe_evidence_items
from app.repositories.evidence_dedupe import dedupe_source_documents


class FeatureRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_match(self, match_id: str) -> MatchModel | None:
        statement = (
            select(MatchModel)
            .where(MatchModel.id == match_id)
            .options(
                joinedload(MatchModel.home_team),
                joinedload(MatchModel.away_team),
                joinedload(MatchModel.competition),
                joinedload(MatchModel.evidence_items),
            )
        )
        return self.session.scalar(statement)

    def count_evidence_items(self, match_id: str) -> int:
        return len(self.list_match_evidence(match_id))

    def count_linked_documents(self, match_id: str) -> int:
        statement = select(SourceDocumentModel).where(SourceDocumentModel.matched_match_id == match_id)
        return len(dedupe_source_documents(list(self.session.scalars(statement))))

    def list_match_evidence(self, match_id: str) -> list[EvidenceItemModel]:
        statement = (
            select(EvidenceItemModel)
            .where(EvidenceItemModel.match_id == match_id)
            .order_by(EvidenceItemModel.captured_at.desc())
        )
        return dedupe_evidence_items(list(self.session.scalars(statement)))

    def list_match_availability(self, match_id: str) -> list[PlayerAvailabilityModel]:
        statement = (
            select(PlayerAvailabilityModel)
            .where(PlayerAvailabilityModel.match_id == match_id)
            .order_by(PlayerAvailabilityModel.impact_score.desc(), PlayerAvailabilityModel.captured_at.desc())
        )
        return list(self.session.scalars(statement))

    def save_snapshot(
        self,
        match_id: str,
        feature_set_version: str,
        payload: dict[str, object],
    ) -> MatchFeatureSnapshotModel:
        snapshot = MatchFeatureSnapshotModel(
            match_id=match_id,
            feature_set_version=feature_set_version,
            payload_json=json.dumps(payload, sort_keys=True),
        )
        self.session.add(snapshot)
        self.session.flush()
        self.session.refresh(snapshot)
        return snapshot
