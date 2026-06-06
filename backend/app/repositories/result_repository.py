from datetime import datetime

from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from app.models.tables import MatchModel
from app.models.tables import MatchResultModel


class ResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_result(self, result: MatchResultModel) -> MatchResultModel:
        existing = self.session.scalar(
            select(MatchResultModel).where(
                MatchResultModel.match_id == result.match_id,
                MatchResultModel.source_id == result.source_id,
                MatchResultModel.played_at == result.played_at,
            )
        )
        if existing is None:
            self.session.add(result)
            self.session.flush()
            self.session.refresh(result)
            return result
        existing.home_goals = result.home_goals
        existing.away_goals = result.away_goals
        existing.result_code = result.result_code
        self.session.add(existing)
        self.session.flush()
        self.session.refresh(existing)
        return existing

    def median_gap_days_for_competition(
        self,
        competition_id: str,
        *,
        team_limit: int = 50,
    ) -> float | None:
        """Median number of days between consecutive matches of the
        same team in this competition.

        Used to size the recent-form window: a 45-day default fits
        weekly club leagues but cuts off national-team and second-tier
        competitions that play once every 6-8 weeks. With this gap we
        can pick a window like ``ceil(3 * gap)`` and stop hand-listing
        the infrequent competitions in code.

        Returns ``None`` when there aren't enough matches in the
        competition to estimate a reliable gap.
        """
        rows = list(
            self.session.scalars(
                select(MatchModel)
                .where(MatchModel.competition_id == competition_id)
                .order_by(MatchModel.kickoff_at)
            )
        )
        if len(rows) < 4:
            return None
        from collections import defaultdict

        team_kickoffs: dict[str, list[datetime]] = defaultdict(list)
        for match in rows:
            team_kickoffs[match.home_team_id].append(match.kickoff_at)
            team_kickoffs[match.away_team_id].append(match.kickoff_at)
        gaps: list[float] = []
        for kickoffs in list(team_kickoffs.values())[:team_limit]:
            kickoffs.sort()
            for previous, current in zip(kickoffs, kickoffs[1:]):
                delta_days = (current - previous).total_seconds() / 86400.0
                if delta_days > 0:
                    gaps.append(delta_days)
        if not gaps:
            return None
        gaps.sort()
        mid = len(gaps) // 2
        if len(gaps) % 2 == 0:
            return (gaps[mid - 1] + gaps[mid]) / 2.0
        return gaps[mid]

    def list_results_for_match(self, match_id: str) -> list[MatchResultModel]:
        statement = (
            select(MatchResultModel)
            .where(MatchResultModel.match_id == match_id)
            .order_by(MatchResultModel.played_at.desc())
        )
        return list(self.session.scalars(statement))

    def get_match(self, match_id: str) -> MatchModel | None:
        return self.session.get(MatchModel, match_id)

    def list_recent_team_results(
        self,
        team_id: str,
        before: datetime,
        limit: int = 8,
    ) -> list[MatchResultModel]:
        statement = (
            select(MatchResultModel)
            .join(MatchModel, MatchModel.id == MatchResultModel.match_id)
            .options(joinedload(MatchResultModel.match))
            .where(
                MatchResultModel.played_at < before,
                or_(
                    MatchModel.home_team_id == team_id,
                    MatchModel.away_team_id == team_id,
                ),
            )
            .order_by(MatchResultModel.played_at.desc())
            .limit(limit * 3)
        )
        return self._dedupe_results(list(self.session.scalars(statement).unique()))[:limit]

    def list_head_to_head_results_for_match(
        self,
        match_id: str,
        limit: int = 5,
    ) -> list[MatchResultModel]:
        match = self.session.get(MatchModel, match_id)
        if match is None:
            return []
        statement = (
            select(MatchResultModel)
            .join(MatchModel, MatchModel.id == MatchResultModel.match_id)
            .options(joinedload(MatchResultModel.match))
            .where(
                MatchResultModel.played_at < match.kickoff_at,
                or_(
                    (MatchModel.home_team_id == match.home_team_id)
                    & (MatchModel.away_team_id == match.away_team_id),
                    (MatchModel.home_team_id == match.away_team_id)
                    & (MatchModel.away_team_id == match.home_team_id),
                ),
            )
            .order_by(MatchResultModel.played_at.desc())
            .limit(limit * 3)
        )
        return self._dedupe_results(list(self.session.scalars(statement).unique()))[:limit]

    def list_context_results_for_match(
        self,
        match_id: str,
        limit_per_team: int = 5,
    ) -> list[MatchResultModel]:
        match = self.session.get(MatchModel, match_id)
        if match is None:
            return []
        results = [
            *self.list_head_to_head_results_for_match(match_id, limit=limit_per_team),
            *self.list_recent_team_results(match.home_team_id, match.kickoff_at, limit=limit_per_team),
            *self.list_recent_team_results(match.away_team_id, match.kickoff_at, limit=limit_per_team),
        ]
        deduped = self._dedupe_results(results)
        return sorted(
            deduped,
            key=lambda item: (self.is_head_to_head(match, item.match), item.played_at),
            reverse=True,
        )

    def is_head_to_head(self, current_match: MatchModel, result_match: MatchModel) -> bool:
        return {
            current_match.home_team_id,
            current_match.away_team_id,
        } == {
            result_match.home_team_id,
            result_match.away_team_id,
        }

    def _dedupe_results(self, results: list[MatchResultModel]) -> list[MatchResultModel]:
        deduped: list[MatchResultModel] = []
        seen: set[tuple[object, ...]] = set()
        for result in results:
            match = result.match
            identity = (
                match.competition_id,
                match.home_team_id,
                match.away_team_id,
                result.played_at,
                result.home_goals,
                result.away_goals,
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(result)
        return deduped
