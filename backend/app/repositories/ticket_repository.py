import json
from datetime import datetime, timezone

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
        superseded_at = datetime.now(timezone.utc)
        previous_stmt = select(TicketRecommendationSnapshotModel).where(
            TicketRecommendationSnapshotModel.slate_id == slate_id,
            TicketRecommendationSnapshotModel.model_version == model_version,
            TicketRecommendationSnapshotModel.is_valid.is_(True),
            (
                TicketRecommendationSnapshotModel.composition_hash.is_(None)
                if composition_hash is None
                else TicketRecommendationSnapshotModel.composition_hash == composition_hash
            ),
        )
        for previous in self.session.scalars(previous_stmt):
            previous.is_valid = False
            previous.invalidated_at = superseded_at
            previous.invalidation_reason = "superseded_by_new_snapshot"

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

    def latest_for_slate(
        self,
        slate_id: str,
        *,
        composition_hash: str | None = None,
        model_version: str | None = None,
    ) -> TicketRecommendationSnapshotModel | None:
        """Return the latest snapshot that is still valid for the current composition."""
        filters = [
            TicketRecommendationSnapshotModel.slate_id == slate_id,
            TicketRecommendationSnapshotModel.is_valid.is_(True),
        ]
        if composition_hash is not None:
            filters.append(TicketRecommendationSnapshotModel.composition_hash == composition_hash)
        if model_version is not None:
            filters.append(TicketRecommendationSnapshotModel.model_version == model_version)
        statement = (
            select(TicketRecommendationSnapshotModel)
            .where(*filters)
            .order_by(
                TicketRecommendationSnapshotModel.generated_at.desc(),
                TicketRecommendationSnapshotModel.id.desc(),
            )
        )
        return self.session.scalar(statement)

    def latest_for_slate_any(self, slate_id: str) -> TicketRecommendationSnapshotModel | None:
        """Return the latest snapshot regardless of validity — for audit use only."""
        statement = (
            select(TicketRecommendationSnapshotModel)
            .where(TicketRecommendationSnapshotModel.slate_id == slate_id)
            .order_by(
                TicketRecommendationSnapshotModel.generated_at.desc(),
                TicketRecommendationSnapshotModel.id.desc(),
            )
        )
        return self.session.scalar(statement)
