"""Live / partial / final match-result tracking.

Sits in front of two stores:

* ``match_live_results`` — the latest live/partial/final observation per
  ``(match_id, source)``. Goals are nullable; an in-progress match has a
  status but may have no canonical final yet.
* ``match_results`` — the canonical FINAL store, untouched by live polling
  except through the explicit promotion below.

Two responsibilities:

1. ``record_observation`` upserts a live observation, never downgrading a
   final observation with a stale/incomplete one, and promotes an
   observation into the canonical store once it is final and complete.
2. ``status_for_matches`` returns a normalized per-match view that merges
   the canonical final result (precedence) with the latest live
   observation, so callers get one ``NormalizedMatchResult`` per match.

It never fabricates a result: a match with no observation simply comes
back as ``scheduled`` / pending with null goals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import MatchResultStatus
from app.models.tables import (
    MatchLiveResultModel,
    MatchResultModel,
    SourceModel,
)
from app.repositories.canonical_result_repository import CanonicalResultRepository

_LIVE_STATUSES = {MatchResultStatus.LIVE, MatchResultStatus.HALFTIME}
_FINAL_STATUSES = {MatchResultStatus.FULL_TIME}


def compute_result_code(home_goals: int | None, away_goals: int | None) -> str | None:
    """Map a scoreline to the Progol outcome code, or None if unknown.

    home > away -> "1", draw -> "X", away > home -> "2".
    """
    if home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return "1"
    if home_goals < away_goals:
        return "2"
    return "X"


@dataclass(frozen=True)
class NormalizedMatchResult:
    match_id: str
    status: MatchResultStatus
    home_goals: int | None
    away_goals: int | None
    result_code: str | None
    minute: int | None
    source: str | None
    source_updated_at: datetime | None
    canonical_result_id: str | None
    is_final: bool
    is_live: bool
    is_pending: bool


class LiveResultService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- ingestion -----------------------------------------------------

    def record_observation(
        self,
        *,
        match_id: str,
        source_id: str,
        status: MatchResultStatus,
        home_goals: int | None = None,
        away_goals: int | None = None,
        minute: int | None = None,
        is_final: bool = False,
        observed_at: datetime | None = None,
        promote_final: bool = True,
        result_code: str | None = None,
    ) -> MatchLiveResultModel:
        """Upsert the latest observation for (match_id, source_id).

        Safety rules:
        * never overwrite an already-final observation with a non-final
          one (a final score is authoritative for that source);
        * never blank out known goals with nulls;
        * compute result_code from goals when both are present, else fall
          back to the explicit ``result_code`` (sign-only results that
          carry the outcome without a scoreline).
        When the observation is final + complete (goals known), promote it
        to the canonical ``match_results`` store (idempotent via
        ResultRepository). Sign-only finals stay in match_live_results.
        """
        observed_at = observed_at or datetime.now(timezone.utc)
        result_code = compute_result_code(home_goals, away_goals) or result_code

        existing = self.session.scalar(
            select(MatchLiveResultModel).where(
                MatchLiveResultModel.match_id == match_id,
                MatchLiveResultModel.source_id == source_id,
            )
        )
        if existing is not None and existing.is_final and not is_final:
            # A finished match cannot un-finish; ignore stale live polls.
            return existing

        if existing is None:
            row = MatchLiveResultModel(
                match_id=match_id,
                source_id=source_id,
                status=status.value,
                home_goals=home_goals,
                away_goals=away_goals,
                result_code=result_code,
                minute=minute,
                is_final=is_final,
                observed_at=observed_at,
                updated_at=observed_at,
            )
            self.session.add(row)
        else:
            row = existing
            row.status = status.value
            if home_goals is not None:
                row.home_goals = home_goals
            if away_goals is not None:
                row.away_goals = away_goals
            row.result_code = (
                compute_result_code(row.home_goals, row.away_goals)
                or result_code
                or row.result_code
            )
            if minute is not None:
                row.minute = minute
            row.is_final = is_final or row.is_final
            row.observed_at = observed_at
            row.updated_at = observed_at
        self.session.flush()

        if (
            promote_final
            and row.is_final
            and row.home_goals is not None
            and row.away_goals is not None
        ):
            self._promote_to_canonical(row, observed_at)
        return row

    def _promote_to_canonical(
        self, row: MatchLiveResultModel, played_at: datetime
    ) -> None:
        """Insert the final score into the canonical match_results store.

        Idempotent: the canonical repo keys on (match_id, source_id,
        played_at), so re-promotion updates the same row rather than
        duplicating. Conflict resolution across sources stays the
        responsibility of CanonicalResultRepository at read time.
        """
        from app.repositories.result_repository import ResultRepository

        assert row.home_goals is not None and row.away_goals is not None
        ResultRepository(self.session).save_result(
            MatchResultModel(
                match_id=row.match_id,
                source_id=row.source_id,
                played_at=played_at,
                home_goals=row.home_goals,
                away_goals=row.away_goals,
                result_code=row.result_code or "X",
            )
        )

    # ---- read / normalize ---------------------------------------------

    def status_for_matches(
        self, match_ids: list[str]
    ) -> dict[str, NormalizedMatchResult]:
        """Return one normalized result per match (final takes precedence).

        Matches with neither a canonical final nor a live observation are
        absent from the dict; callers treat them as scheduled/pending.
        """
        if not match_ids:
            return {}

        canonical = CanonicalResultRepository(self.session).get_canonical_for_matches(
            match_ids
        )
        live_by_match = self._best_live_by_match(match_ids)
        source_names = self._source_names()

        out: dict[str, NormalizedMatchResult] = {}
        for match_id in match_ids:
            final = canonical.get(match_id)
            live = live_by_match.get(match_id)
            if final is not None:
                # Canonical final wins; surface its scoreline + code.
                out[match_id] = NormalizedMatchResult(
                    match_id=match_id,
                    status=MatchResultStatus.FULL_TIME,
                    home_goals=final.home_goals,
                    away_goals=final.away_goals,
                    result_code=final.result_code,
                    minute=None,
                    source=source_names.get(final.source_id),
                    source_updated_at=final.played_at,
                    canonical_result_id=final.id,
                    is_final=True,
                    is_live=False,
                    is_pending=False,
                )
            elif live is not None:
                status = _coerce_status(live.status)
                is_final = live.is_final
                is_live = status in _LIVE_STATUSES and not is_final
                out[match_id] = NormalizedMatchResult(
                    match_id=match_id,
                    status=status,
                    home_goals=live.home_goals,
                    away_goals=live.away_goals,
                    result_code=live.result_code,
                    minute=live.minute,
                    source=source_names.get(live.source_id),
                    source_updated_at=live.updated_at,
                    canonical_result_id=None,
                    is_final=is_final,
                    is_live=is_live,
                    is_pending=not is_final and not is_live,
                )
        return out

    def _best_live_by_match(
        self, match_ids: list[str]
    ) -> dict[str, MatchLiveResultModel]:
        """Highest-priority live observation per match (lowest priority no.)."""
        rows = self.session.execute(
            select(MatchLiveResultModel, SourceModel.result_source_priority)
            .join(SourceModel, MatchLiveResultModel.source_id == SourceModel.id)
            .where(MatchLiveResultModel.match_id.in_(match_ids))
            .order_by(SourceModel.result_source_priority)
        ).all()
        best: dict[str, MatchLiveResultModel] = {}
        best_priority: dict[str, int] = {}
        for row, priority in rows:
            prio = priority if priority is not None else 1_000_000
            if row.match_id not in best or prio < best_priority[row.match_id]:
                best[row.match_id] = row
                best_priority[row.match_id] = prio
        return best

    def _source_names(self) -> dict[str, str]:
        return {
            sid: name
            for sid, name in self.session.execute(
                select(SourceModel.id, SourceModel.name)
            ).all()
        }


def _coerce_status(raw: str | None) -> MatchResultStatus:
    try:
        return MatchResultStatus(raw)
    except (ValueError, TypeError):
        return MatchResultStatus.UNKNOWN
