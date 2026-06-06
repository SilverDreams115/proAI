from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import ProgolJornadaScoreModel


class JornadaScoreRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_score(self, score: ProgolJornadaScoreModel) -> ProgolJornadaScoreModel:
        existing = self._find_by_slate_version(score.slate_id, score.composition_hash)
        if existing is not None:
            existing.draw_code = score.draw_code
            existing.week_type = score.week_type
            existing.slate_version = score.slate_version
            existing.total_matches = score.total_matches
            existing.matches_with_results = score.matches_with_results
            existing.simple_hits = score.simple_hits
            existing.simple_hit_rate = score.simple_hit_rate
            existing.ticket_hits = score.ticket_hits
            existing.ticket_hit_rate = score.ticket_hit_rate
            existing.brier_score_avg = score.brier_score_avg
            existing.high_confidence_hits = score.high_confidence_hits
            existing.high_confidence_total = score.high_confidence_total
            existing.medium_confidence_hits = score.medium_confidence_hits
            existing.medium_confidence_total = score.medium_confidence_total
            existing.low_confidence_hits = score.low_confidence_hits
            existing.low_confidence_total = score.low_confidence_total
            existing.blocked_hits = score.blocked_hits
            existing.blocked_total = score.blocked_total
            existing.details_json = score.details_json
            existing.computed_at = datetime.now(timezone.utc)
            existing.is_complete = score.is_complete
            self.session.add(existing)
            self.session.flush()
            return existing
        self.session.add(score)
        self.session.flush()
        return score

    def get_latest_for_slate(self, slate_id: str) -> ProgolJornadaScoreModel | None:
        stmt = (
            select(ProgolJornadaScoreModel)
            .where(ProgolJornadaScoreModel.slate_id == slate_id)
            .order_by(ProgolJornadaScoreModel.computed_at.desc())
        )
        return self.session.scalar(stmt)

    def list_history(self, *, limit: int = 50) -> list[ProgolJornadaScoreModel]:
        stmt = (
            select(ProgolJornadaScoreModel)
            .order_by(ProgolJornadaScoreModel.computed_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def _find_by_slate_version(
        self, slate_id: str, composition_hash: str | None
    ) -> ProgolJornadaScoreModel | None:
        if composition_hash is None:
            return None
        stmt = select(ProgolJornadaScoreModel).where(
            ProgolJornadaScoreModel.slate_id == slate_id,
            ProgolJornadaScoreModel.composition_hash == composition_hash,
        )
        return self.session.scalar(stmt)
