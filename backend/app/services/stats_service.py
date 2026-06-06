from app.models.tables import MatchStatSnapshotModel
from app.models.tables import TeamStatSnapshotModel
from app.repositories.stats_repository import StatsRepository


class StatsService:
    def __init__(self, repository: StatsRepository) -> None:
        self.repository = repository

    def list_team_stats(self, team_id: str):
        return self.repository.list_team_stats(team_id)

    def list_match_stats(self, match_id: str):
        return self.repository.list_match_stats(match_id)

    def persist_team_stat(self, snapshot: TeamStatSnapshotModel):
        return self.repository.save_team_stat(snapshot)

    def persist_match_stat(self, snapshot: MatchStatSnapshotModel):
        return self.repository.save_match_stat(snapshot)
