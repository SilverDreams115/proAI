import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import TicketRecommendationSnapshotModel


class TicketRecommendationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_snapshot(
        self,
        *,
        slate_id: str,
        model_version: str,
        payload: dict[str, object],
        composition_hash: str | None = None,
    ) -> TicketRecommendationSnapshotModel:
        snapshot = TicketRecommendationSnapshotModel(
            slate_id=slate_id,
            model_version=model_version,
            payload_json=json.dumps(payload, sort_keys=True),
            composition_hash=composition_hash,
            is_valid=True,
        )
        self.session.add(snapshot)
        self.session.flush()
        self.session.refresh(snapshot)
        return snapshot

    def latest_for_slate(self, slate_id: str) -> TicketRecommendationSnapshotModel | None:
        """Return the latest snapshot that is still valid for the current composition."""
        statement = (
            select(TicketRecommendationSnapshotModel)
            .where(
                TicketRecommendationSnapshotModel.slate_id == slate_id,
                TicketRecommendationSnapshotModel.is_valid.is_(True),
            )
            .order_by(TicketRecommendationSnapshotModel.generated_at.desc())
        )
        return self.session.scalar(statement)

    def latest_for_slate_any(self, slate_id: str) -> TicketRecommendationSnapshotModel | None:
        """Return the latest snapshot regardless of validity — for audit use only."""
        statement = (
            select(TicketRecommendationSnapshotModel)
            .where(TicketRecommendationSnapshotModel.slate_id == slate_id)
            .order_by(TicketRecommendationSnapshotModel.generated_at.desc())
        )
        return self.session.scalar(statement)
