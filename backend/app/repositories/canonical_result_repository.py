"""Canonical result selection.

A match may have results from multiple sources (e.g. TheSportsDB and
football-data.org). This repository resolves conflicts using
``SourceModel.result_source_priority``:

* If all sources agree on ``result_code`` → canonical = highest-priority
  source (lowest priority number).
* If sources disagree → the match is **conflicting** and excluded from
  scoring and dataset export until an operator resolves it.
* If no results exist for a match → the match is simply absent from the
  returned dict.

This module is intentionally import-independent of the service layer so
it can be used from both ``JornadaScoringService`` and
``AdaptiveDatasetService`` without circular dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchResultModel, SourceModel


class CanonicalResult(NamedTuple):
    result: MatchResultModel
    is_conflicting: bool


class CanonicalResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_canonical_for_matches(
        self, match_ids: list[str]
    ) -> dict[str, MatchResultModel]:
        """Return one non-conflicting canonical result per match_id.

        Match IDs with no results or with conflicting result_codes across
        sources are excluded from the returned dict. Callers can compare
        the returned keys against the original ``match_ids`` to detect
        which matches are missing (no result) or conflicting.
        """
        if not match_ids:
            return {}

        rows = self.session.execute(
            select(MatchResultModel, SourceModel.result_source_priority)
            .join(SourceModel, MatchResultModel.source_id == SourceModel.id)
            .where(MatchResultModel.match_id.in_(match_ids))
            .order_by(SourceModel.result_source_priority)
        ).all()

        by_match: dict[str, list[tuple[MatchResultModel, int]]] = defaultdict(list)
        for result, priority in rows:
            by_match[result.match_id].append((result, priority))

        canonical: dict[str, MatchResultModel] = {}
        for match_id, entries in by_match.items():
            result_codes = {r.result_code for r, _ in entries}
            if len(result_codes) > 1:
                continue  # conflict — exclude
            best = min(entries, key=lambda x: x[1])
            canonical[match_id] = best[0]

        return canonical

    def get_with_conflict_info(
        self, match_ids: list[str]
    ) -> dict[str, CanonicalResult]:
        """Like get_canonical_for_matches but also returns conflicting entries.

        Returns ALL match_ids that have at least one result, flagging
        conflicting ones so callers can surface the discrepancy.
        """
        if not match_ids:
            return {}

        rows = self.session.execute(
            select(MatchResultModel, SourceModel.result_source_priority)
            .join(SourceModel, MatchResultModel.source_id == SourceModel.id)
            .where(MatchResultModel.match_id.in_(match_ids))
            .order_by(SourceModel.result_source_priority)
        ).all()

        by_match: dict[str, list[tuple[MatchResultModel, int]]] = defaultdict(list)
        for result, priority in rows:
            by_match[result.match_id].append((result, priority))

        out: dict[str, CanonicalResult] = {}
        for match_id, entries in by_match.items():
            result_codes = {r.result_code for r, _ in entries}
            is_conflicting = len(result_codes) > 1
            # Always return the highest-priority entry as representative
            best = min(entries, key=lambda x: x[1])
            out[match_id] = CanonicalResult(result=best[0], is_conflicting=is_conflicting)

        return out

    def conflict_match_ids(self, match_ids: list[str]) -> set[str]:
        """Return the subset of match_ids that have conflicting result codes."""
        if not match_ids:
            return set()
        rows = self.session.execute(
            select(MatchResultModel.match_id, MatchResultModel.result_code)
            .where(MatchResultModel.match_id.in_(match_ids))
        ).all()
        by_match: dict[str, set[str]] = defaultdict(set)
        for mid, code in rows:
            by_match[mid].add(code)
        return {mid for mid, codes in by_match.items() if len(codes) > 1}
