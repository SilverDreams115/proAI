from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchStatSnapshotModel
from app.models.tables import TeamStatSnapshotModel


class StatsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_team_stat(self, snapshot: TeamStatSnapshotModel) -> TeamStatSnapshotModel:
        existing = self.session.scalar(
            select(TeamStatSnapshotModel).where(
                TeamStatSnapshotModel.team_id == snapshot.team_id,
                TeamStatSnapshotModel.source_id == snapshot.source_id,
                TeamStatSnapshotModel.captured_at == snapshot.captured_at,
                TeamStatSnapshotModel.stat_type == snapshot.stat_type,
            )
        )
        if existing is None:
            self.session.add(snapshot)
            self.session.flush()
            self.session.refresh(snapshot)
            return snapshot
        existing.value = snapshot.value
        existing.sample_size = snapshot.sample_size
        self.session.add(existing)
        self.session.flush()
        self.session.refresh(existing)
        return existing

    def save_match_stat(self, snapshot: MatchStatSnapshotModel) -> MatchStatSnapshotModel:
        existing = self.session.scalar(
            select(MatchStatSnapshotModel).where(
                MatchStatSnapshotModel.match_id == snapshot.match_id,
                MatchStatSnapshotModel.source_id == snapshot.source_id,
                MatchStatSnapshotModel.captured_at == snapshot.captured_at,
                MatchStatSnapshotModel.stat_type == snapshot.stat_type,
            )
        )
        if existing is None:
            self.session.add(snapshot)
            self.session.flush()
            self.session.refresh(snapshot)
            return snapshot
        existing.home_value = snapshot.home_value
        existing.away_value = snapshot.away_value
        self.session.add(existing)
        self.session.flush()
        self.session.refresh(existing)
        return existing

    def list_team_stats(self, team_id: str) -> list[TeamStatSnapshotModel]:
        statement = (
            select(TeamStatSnapshotModel)
            .where(TeamStatSnapshotModel.team_id == team_id)
            .order_by(TeamStatSnapshotModel.captured_at.desc())
        )
        return list(self.session.scalars(statement))

    def list_match_stats(self, match_id: str) -> list[MatchStatSnapshotModel]:
        statement = (
            select(MatchStatSnapshotModel)
            .where(MatchStatSnapshotModel.match_id == match_id)
            .order_by(MatchStatSnapshotModel.captured_at.desc())
        )
        return list(self.session.scalars(statement))
