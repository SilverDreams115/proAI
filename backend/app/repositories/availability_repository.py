import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PlayerAvailabilityModel


class AvailabilityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_match_availability(self, match_id: str) -> list[PlayerAvailabilityModel]:
        statement = (
            select(PlayerAvailabilityModel)
            .where(PlayerAvailabilityModel.match_id == match_id)
            .order_by(PlayerAvailabilityModel.impact_score.desc(), PlayerAvailabilityModel.captured_at.desc())
        )
        return self._dedupe_availability(list(self.session.scalars(statement)))

    def _dedupe_availability(self, items: list[PlayerAvailabilityModel]) -> list[PlayerAvailabilityModel]:
        deduped: list[PlayerAvailabilityModel] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in items:
            identity = (
                item.team_id,
                item.player_name.strip().lower(),
                item.status,
                item.category,
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(item)
        return deduped

    def save_availability(
        self,
        *,
        match_id: str,
        team_id: str,
        player_id: str | None,
        source_id: str,
        evidence_id: str | None,
        captured_at,
        status: str,
        category: str,
        player_name: str,
        detail: str,
        confidence: float,
        impact_score: float,
        payload: dict[str, object],
    ) -> PlayerAvailabilityModel:
        existing = self.session.scalar(
            select(PlayerAvailabilityModel).where(
                PlayerAvailabilityModel.match_id == match_id,
                PlayerAvailabilityModel.team_id == team_id,
                PlayerAvailabilityModel.player_name == player_name,
                PlayerAvailabilityModel.status == status,
                PlayerAvailabilityModel.category == category,
                PlayerAvailabilityModel.source_id == source_id,
                PlayerAvailabilityModel.captured_at == captured_at,
            )
        )
        if existing is None:
            item = PlayerAvailabilityModel(
                match_id=match_id,
                team_id=team_id,
                player_id=player_id,
                source_id=source_id,
                evidence_id=evidence_id,
                captured_at=captured_at,
                status=status,
                category=category,
                player_name=player_name,
                detail=detail,
                confidence=confidence,
                impact_score=impact_score,
                payload_json=json.dumps(payload, sort_keys=True),
            )
            self.session.add(item)
            self.session.flush()
            self.session.refresh(item)
            return item
        existing.player_id = player_id
        existing.evidence_id = evidence_id
        existing.detail = detail
        existing.confidence = confidence
        existing.impact_score = impact_score
        existing.payload_json = json.dumps(payload, sort_keys=True)
        self.session.add(existing)
        self.session.flush()
        self.session.refresh(existing)
        return existing
