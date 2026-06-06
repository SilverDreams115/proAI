"""Best-effort matching of Progol PDF fixture pairs to real DB entities.

The LN guide PDF only carries strings like "MÉXICO vs AUSTRALIA". The
ingestion pipeline already populates `matches` from FootballData / TSDB
sources, so any pair that exists there can be promoted with the real
competition, kickoff and venue instead of placeholder values.

This is intentionally read-only — it never creates new teams or
competitions. When a pair doesn't resolve we tell the caller and let it
fall back to a synthetic placeholder fixture. That preserves the
"sólido como diamante" honesty principle: a promoted slate never
silently invents a competition that doesn't exist in our data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.tables import MatchModel
from app.repositories.entity_repository import EntityRepository
from app.services.normalization_service import NormalizationService

logger = logging.getLogger("proai.progol_fixture_resolver")


@dataclass(frozen=True)
class ResolvedFixture:
    home_name: str
    away_name: str
    home_country: str | None
    away_country: str | None
    competition_name: str
    competition_country: str | None
    competition_season: str | None
    kickoff_at: datetime
    venue: str | None
    matched: bool  # False when this is a fallback placeholder


class ProgolFixtureResolver:
    """Look up each Progol fixture pair against existing matches in the
    DB. Caller supplies the venta cierre and we search a window starting
    a few hours before (some friendlies kick off the same morning) and
    extending several days after."""

    # Search window: -12h to +96h around cierre. Most regular fixtures
    # are within 72h but friendlies and double-rounds can land at +96h.
    DEFAULT_WINDOW_BEFORE_HOURS = 12
    DEFAULT_WINDOW_AFTER_HOURS = 96

    def __init__(
        self,
        session: Session,
        normalization_service: NormalizationService | None = None,
        window_before: timedelta | None = None,
        window_after: timedelta | None = None,
    ) -> None:
        self.session = session
        self.repo = EntityRepository(session)
        self.normalizer = normalization_service or NormalizationService()
        self.window_before = window_before or timedelta(hours=self.DEFAULT_WINDOW_BEFORE_HOURS)
        self.window_after = window_after or timedelta(hours=self.DEFAULT_WINDOW_AFTER_HOURS)

    def resolve_pair(
        self,
        home_name: str,
        away_name: str,
        cierre: datetime,
    ) -> MatchModel | None:
        """Return the upcoming match for this pair around `cierre`, or
        None when either team is unknown or no match falls in the
        window. The returned MatchModel is eager-loaded with its teams
        and competition so callers can build a payload without extra
        queries."""
        home_team = self.repo.find_team_by_alias(
            home_name, self.normalizer.normalize_team_name(home_name)
        )
        away_team = self.repo.find_team_by_alias(
            away_name, self.normalizer.normalize_team_name(away_name)
        )
        if home_team is None or away_team is None:
            return None
        window_start = cierre - self.window_before
        window_end = cierre + self.window_after
        return self.repo.find_upcoming_match_for_pair(
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            window_start=window_start,
            window_end=window_end,
        )

    def infer_competition_for_pair(self, home_name: str, away_name: str):
        """Best guess at the competition this pair "belongs to" when no
        upcoming match is present. Tries shared history first, then each
        team alone. Returns None when neither team is known to us — that
        case still falls back to the synthetic placeholder competition
        so we never invent league metadata out of nothing."""
        home_team = self.repo.find_team_by_alias(
            home_name, self.normalizer.normalize_team_name(home_name)
        )
        away_team = self.repo.find_team_by_alias(
            away_name, self.normalizer.normalize_team_name(away_name)
        )
        if home_team is not None and away_team is not None:
            shared = self.repo.most_played_competition_for_pair(
                home_team_id=home_team.id, away_team_id=away_team.id
            )
            if shared is not None:
                return shared
        for team in (home_team, away_team):
            if team is None:
                continue
            single = self.repo.most_played_competition_for_team(team.id)
            if single is not None:
                return single
        return None

    def resolve_many(
        self,
        pairs: Iterable[tuple[int, str, str]],
        cierre: datetime,
    ) -> dict[int, MatchModel]:
        """Bulk-resolve a list of (position, home, away) tuples and
        return a {position: MatchModel} dict for the ones that matched.
        Unmatched positions are simply absent from the dict so the
        caller can decide on the placeholder strategy."""
        resolved: dict[int, MatchModel] = {}
        for position, home, away in pairs:
            match = self.resolve_pair(home, away, cierre)
            if match is not None:
                resolved[int(position)] = match
                logger.info(
                    "progol fixture resolved",
                    extra={
                        "event": "progol_fixture_resolved",
                        "position": position,
                        "home": home,
                        "away": away,
                        "competition": match.competition.name,
                        "kickoff_at": match.kickoff_at.isoformat(),
                    },
                )
            else:
                logger.info(
                    "progol fixture unresolved — falling back to placeholder",
                    extra={
                        "event": "progol_fixture_unresolved",
                        "position": position,
                        "home": home,
                        "away": away,
                    },
                )
        return resolved
