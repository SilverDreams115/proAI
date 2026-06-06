from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.models.tables import CompetitionAliasModel
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PlayerModel
from app.models.tables import TeamPlayerModel
from app.models.tables import TeamAliasModel
from app.models.tables import TeamModel


class EntityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_team_by_alias(self, alias: str, normalized_alias: str) -> TeamModel | None:
        # Real (non-placeholder) rows always win. A placeholder team
        # created during slate promotion (e.g., "Tampico") would
        # otherwise shadow the real row (e.g., "Tampico Madero")
        # because Postgres returns rows in an undefined order — see the
        # bug we hit with Tampico in May 2026. Order by is_placeholder
        # ASC so FALSE (real) comes before TRUE (placeholder).
        statement = (
            select(TeamModel)
            .outerjoin(TeamAliasModel, TeamAliasModel.team_id == TeamModel.id)
            .where(or_(TeamModel.name == alias, TeamAliasModel.normalized_alias == normalized_alias))
            .order_by(TeamModel.is_placeholder.asc())
        )
        return self.session.scalar(statement)

    def find_competition_by_alias(self, alias: str, normalized_alias: str) -> CompetitionModel | None:
        statement = (
            select(CompetitionModel)
            .outerjoin(CompetitionAliasModel, CompetitionAliasModel.competition_id == CompetitionModel.id)
            .where(
                or_(
                    CompetitionModel.name == alias,
                    CompetitionAliasModel.normalized_alias == normalized_alias,
                )
            )
            .order_by(CompetitionModel.is_placeholder.asc())
        )
        return self.session.scalar(statement)

    def attach_team_alias(self, team: TeamModel, alias: str, normalized_alias: str) -> None:
        exists = self.session.scalar(
            select(TeamAliasModel).where(
                or_(
                    TeamAliasModel.alias == alias,
                    TeamAliasModel.normalized_alias == normalized_alias,
                )
            )
        )
        if exists is None:
            self.session.add(TeamAliasModel(team=team, alias=alias, normalized_alias=normalized_alias))

    def attach_competition_alias(
        self,
        competition: CompetitionModel,
        alias: str,
        normalized_alias: str,
    ) -> None:
        exists = self.session.scalar(
            select(CompetitionAliasModel).where(
                or_(
                    CompetitionAliasModel.alias == alias,
                    CompetitionAliasModel.normalized_alias == normalized_alias,
                )
            )
        )
        if exists is None:
            self.session.add(
                CompetitionAliasModel(
                    competition=competition,
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            )

    def list_matches(self) -> list[MatchModel]:
        # F6.3: drop the joinedload of evidence_items / source_documents /
        # results. Each carries one row per related entity, so the
        # cartesian product grew memory cubically with the dataset (16 GB
        # at ~2000 matches). The relationships are still available via
        # SQLAlchemy lazy loading when an individual match needs them.
        # `competition`, `home_team`, and `away_team` stay joined because
        # every caller reads at least one of those.
        statement = (
            select(MatchModel)
            .options(
                joinedload(MatchModel.home_team),
                joinedload(MatchModel.away_team),
                joinedload(MatchModel.competition),
            )
            .order_by(MatchModel.kickoff_at.desc())
        )
        return list(self.session.scalars(statement).unique())

    def resolve_team(self, name: str, normalized_alias: str) -> TeamModel | None:
        return self.find_team_by_alias(name, normalized_alias)

    def find_player_by_normalized_name(self, normalized_name: str) -> PlayerModel | None:
        statement = select(PlayerModel).where(PlayerModel.normalized_name == normalized_name)
        return self.session.scalar(statement)

    def find_upcoming_match_for_pair(
        self,
        *,
        home_team_id: str,
        away_team_id: str,
        window_start,
        window_end,
    ) -> MatchModel | None:
        # Used by the Progol fixture resolver to find a real upcoming
        # match for a (home, away) pair without caring which competition
        # it belongs to. The kickoff_at window is centered on the venta
        # cierre — most fixtures kick off 12-72h after the operator can
        # still register the boleta.
        statement = (
            select(MatchModel)
            .where(
                MatchModel.home_team_id == home_team_id,
                MatchModel.away_team_id == away_team_id,
                MatchModel.kickoff_at >= window_start,
                MatchModel.kickoff_at <= window_end,
            )
            .options(
                joinedload(MatchModel.home_team),
                joinedload(MatchModel.away_team),
                joinedload(MatchModel.competition),
            )
            .order_by(MatchModel.kickoff_at.asc())
        )
        return self.session.scalar(statement)

    def most_played_competition_for_pair(
        self,
        *,
        home_team_id: str,
        away_team_id: str,
    ) -> CompetitionModel | None:
        # Most frequent competition where these two teams have ever met,
        # either home/away or reversed. Used by promote_proposal to
        # assign a sensible competition tag when no upcoming match
        # exists in the DB — keeps the readiness policy lookup honest
        # instead of pinning unknown placeholders to "unclassified".
        statement = (
            select(CompetitionModel, func.count(MatchModel.id).label("c"))
            .join(MatchModel, MatchModel.competition_id == CompetitionModel.id)
            .where(
                or_(
                    (MatchModel.home_team_id == home_team_id) & (MatchModel.away_team_id == away_team_id),
                    (MatchModel.home_team_id == away_team_id) & (MatchModel.away_team_id == home_team_id),
                )
            )
            .group_by(CompetitionModel.id)
            .order_by(func.count(MatchModel.id).desc())
            .limit(1)
        )
        row = self.session.execute(statement).first()
        return row[0] if row else None

    def most_played_competition_for_team(self, team_id: str) -> CompetitionModel | None:
        statement = (
            select(CompetitionModel, func.count(MatchModel.id).label("c"))
            .join(MatchModel, MatchModel.competition_id == CompetitionModel.id)
            .where(
                or_(
                    MatchModel.home_team_id == team_id,
                    MatchModel.away_team_id == team_id,
                )
            )
            .group_by(CompetitionModel.id)
            .order_by(func.count(MatchModel.id).desc())
            .limit(1)
        )
        row = self.session.execute(statement).first()
        return row[0] if row else None

    def find_match_by_identity(
        self,
        *,
        competition_id: str,
        home_team_id: str,
        away_team_id: str,
        kickoff_at,
    ) -> MatchModel | None:
        statement = (
            select(MatchModel)
            .where(
                MatchModel.competition_id == competition_id,
                MatchModel.home_team_id == home_team_id,
                MatchModel.away_team_id == away_team_id,
                MatchModel.kickoff_at == kickoff_at,
            )
            .options(
                joinedload(MatchModel.home_team),
                joinedload(MatchModel.away_team),
                joinedload(MatchModel.competition),
            )
        )
        return self.session.scalar(statement)

    def attach_player_to_team(
        self,
        team: TeamModel,
        player: PlayerModel,
        squad_role: str | None,
    ) -> None:
        statement = select(TeamPlayerModel).where(
            TeamPlayerModel.team_id == team.id,
            TeamPlayerModel.player_id == player.id,
        )
        existing = self.session.scalar(statement)
        if existing is None:
            self.session.add(
                TeamPlayerModel(
                    team=team,
                    player=player,
                    squad_role=squad_role,
                    is_active=True,
                )
            )

    def find_match_by_participants(
        self,
        competition_name: str,
        home_team_name: str,
        away_team_name: str,
    ) -> MatchModel | None:
        for match in self.list_matches():
            if (
                match.competition.name == competition_name
                and match.home_team.name == home_team_name
                and match.away_team.name == away_team_name
            ):
                return match
        return None
