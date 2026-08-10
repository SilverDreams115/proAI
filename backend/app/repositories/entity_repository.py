from datetime import timedelta, timezone

from sqlalchemy import case
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

# Same tolerance the result dedupe uses: feeds disagree by an hour when one
# stores local kickoff against UTC, and by a day when one dates a late kickoff
# by the calendar day it ends on. A rematch is never this close.
_NEAR_IDENTITY_TOLERANCE = timedelta(hours=48)


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
            .order_by(
                TeamModel.is_placeholder.asc(),
                case((TeamAliasModel.normalized_alias == normalized_alias, 0), else_=1),
                TeamModel.name.asc(),
            )
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
        #
        # Placeholder rows are excluded on purpose. A previous slate that
        # could not resolve this same pair fabricated a row with a kickoff
        # derived from ITS cierre; returning that here would report an
        # invented fixture as the real match found and copy the invented
        # kickoff into the new slate. Sixteen match rows were already shared
        # between two slates that way (PG-2336/PGM-799, PG-2337/PGM-800).
        # A pair we have never really ingested must keep falling through to
        # the fallback, which at least marks what it builds.
        statement = (
            select(MatchModel)
            .where(
                MatchModel.home_team_id == home_team_id,
                MatchModel.away_team_id == away_team_id,
                MatchModel.kickoff_at >= window_start,
                MatchModel.kickoff_at <= window_end,
                MatchModel.is_placeholder.is_(False),
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

    def find_match_near_identity(
        self,
        *,
        competition_id: str,
        home_team_id: str,
        away_team_id: str,
        kickoff_at,
        tolerance=_NEAR_IDENTITY_TOLERANCE,
    ) -> MatchModel | None:
        """Same fixture, allowing the feeds to disagree about the kickoff.

        ``find_match_by_identity`` demands an exact timestamp, which is what
        ``uq_matches_fixture_identity`` enforces. Callers that create a row on
        a miss therefore mint a second row for one real fixture whenever their
        source states the kickoff an hour off — and the two then split the
        evidence and the result between them. The results path already guards
        against this with its own nearby lookup; this is the same idea, shared.

        Fabricated rows are excluded rather than merely ranked last, for the
        reason v32 recorded: a construction is not evidence that a fixture
        exists at that hour, and returning one copies an invented kickoff into
        a new slate. Re-promoting the same slate still finds its own row
        through the exact-kickoff lookup, so idempotency does not depend on
        this one.
        """
        if kickoff_at.tzinfo is None:
            kickoff_at = kickoff_at.replace(tzinfo=timezone.utc)
        statement = (
            select(MatchModel)
            .where(
                MatchModel.competition_id == competition_id,
                MatchModel.home_team_id == home_team_id,
                MatchModel.away_team_id == away_team_id,
                MatchModel.kickoff_at >= kickoff_at - tolerance,
                MatchModel.kickoff_at <= kickoff_at + tolerance,
                MatchModel.is_placeholder.is_(False),
            )
            .options(
                joinedload(MatchModel.home_team),
                joinedload(MatchModel.away_team),
                joinedload(MatchModel.competition),
            )
        )
        best: MatchModel | None = None
        best_key: tuple[bool, float] | None = None
        for candidate in self.session.scalars(statement).unique().all():
            candidate_kickoff = candidate.kickoff_at
            if candidate_kickoff.tzinfo is None:
                candidate_kickoff = candidate_kickoff.replace(tzinfo=timezone.utc)
            key = (
                bool(candidate.is_placeholder),
                abs((candidate_kickoff - kickoff_at).total_seconds()),
            )
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        return best

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
