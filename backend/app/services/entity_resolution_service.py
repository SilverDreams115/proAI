from app.models.tables import CompetitionModel
from app.models.tables import TeamModel
from app.repositories.entity_repository import EntityRepository
from app.services.normalization_service import NormalizationService
from app.services.team_name_quality_service import is_suspicious_team_name


class EntityResolutionService:
    def __init__(
        self,
        repository: EntityRepository,
        normalization_service: NormalizationService | None = None,
    ) -> None:
        self.repository = repository
        self.normalization_service = normalization_service or NormalizationService()

    def resolve_team(
        self,
        name: str,
        country: str | None,
        *,
        is_placeholder: bool = False,
    ) -> TeamModel:
        is_placeholder = bool(is_placeholder or is_suspicious_team_name(name))
        normalized = self.normalization_service.normalize_team_name(name)
        team = self.repository.find_team_by_alias(name, normalized)
        if team is None:
            team = TeamModel(name=name, country=country, is_placeholder=is_placeholder)
            self.repository.session.add(team)
            self.repository.session.flush()
        elif team.is_placeholder and not is_placeholder:
            # A real ingestion is upgrading a row that was created as a
            # placeholder by an earlier slate promotion. Promote the
            # row in place rather than leave the flag stuck on TRUE.
            team.is_placeholder = False
        elif is_placeholder and is_suspicious_team_name(team.name):
            # Single-letter / TBD-like teams are not trustworthy canonical
            # entities. If one was created before the placeholder guard existed,
            # downgrade it so future diagnostics and training filters see it.
            team.is_placeholder = True
        self.repository.attach_team_alias(team, name, normalized)
        return team

    def resolve_competition(
        self,
        name: str,
        country: str | None,
        season: str | None,
        *,
        is_placeholder: bool = False,
    ) -> CompetitionModel:
        normalized = self.normalization_service.normalize_competition_name(name)
        competition = self.repository.find_competition_by_alias(name, normalized)
        if competition is None:
            competition = CompetitionModel(
                name=name,
                country=country,
                season=season,
                is_placeholder=is_placeholder,
            )
            self.repository.session.add(competition)
            self.repository.session.flush()
        elif competition.is_placeholder and not is_placeholder:
            competition.is_placeholder = False
        self.repository.attach_competition_alias(competition, name, normalized)
        return competition
