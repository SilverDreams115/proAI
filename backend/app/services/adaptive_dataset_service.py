"""Adaptive dataset builder.

Assembles training-ready rows from scored Progol jornadas. Each row
combines:
  - Prediction probabilities from ``PredictionModel`` (via
    ``details_json`` already computed by ``JornadaScoringService``).
  - Canonical result from ``CanonicalResultRepository``.
  - Ticket picks from the valid ``TicketRecommendationSnapshotModel``.

Rows are excluded when:
  - The jornada has no result yet (``is_complete=False``) unless the
    caller passes ``include_partial=True``.
  - The match has no canonical result (conflicting sources).
  - The prediction is missing (no linked prediction for this
    slate_id + composition_hash).
  - The jornada score has no ``composition_hash`` (pre-backfill slate).

This module must NOT retrain the model.  It only reads existing data
and assembles a structured export.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.schemas.adaptive_dataset import (
    AdaptiveDatasetRow,
    AdaptiveDatasetSummary,
    ConfidenceBandDatasetStats,
)

logger = logging.getLogger(__name__)

_BANDS = ("high", "medium", "low", "blocked")


class AdaptiveDatasetService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_rows_for_slate(
        self,
        slate_id: str,
        *,
        include_partial: bool = False,
    ) -> list[AdaptiveDatasetRow]:
        """Return training rows for one slate.

        Returns an empty list when:
          - No jornada score exists for this slate.
          - The score has no composition_hash.
          - ``include_partial=False`` and the jornada is incomplete.
        """
        score = JornadaScoreRepository(self.session).get_latest_for_slate(slate_id)
        if score is None or not score.composition_hash:
            return []
        if not include_partial and not score.is_complete:
            return []

        try:
            details: list[dict[str, Any]] = json.loads(score.details_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

        match_ids = [d["match_id"] for d in details if d.get("match_id")]
        canonical_map = CanonicalResultRepository(self.session).get_canonical_for_matches(match_ids)
        blocked_map = self._blocked_reasons(score.slate_id, score.composition_hash, match_ids)

        rows: list[AdaptiveDatasetRow] = []
        for detail in details:
            match_id = detail.get("match_id")
            if not match_id:
                continue
            # Must have a result recorded in the scoring detail
            if detail.get("result_code") is None:
                continue
            # Must have a canonical (non-conflicting) result in DB
            if match_id not in canonical_map:
                continue
            # Must have a prediction linked to this slate/hash
            if detail.get("recommended_outcome") is None:
                continue

            ticket_modes = detail.get("ticket_modes") or {}
            rows.append(self._build_row(score, detail, ticket_modes, blocked_map.get(match_id)))

        return rows

    def build_summary(self, *, include_partial: bool = False) -> AdaptiveDatasetSummary:
        """Aggregate dataset stats across all scored jornadas."""
        scores = JornadaScoreRepository(self.session).list_history(limit=1000)

        total_slates_scored = len(scores)
        total_slates_complete = sum(1 for s in scores if s.is_complete)
        total_rows = 0
        rows_with_result = 0
        rows_with_conflict = 0
        rows_with_ticket_info = 0
        total_hits = 0
        brier_accum: list[float] = []
        band_stats: dict[str, dict[str, int]] = {b: {"total": 0, "hits": 0} for b in _BANDS}

        for score in scores:
            if not score.composition_hash:
                continue

            try:
                details: list[dict[str, Any]] = json.loads(score.details_json or "[]")
            except (json.JSONDecodeError, TypeError):
                continue

            match_ids = [d["match_id"] for d in details if d.get("match_id")]
            canonical_map = CanonicalResultRepository(self.session).get_canonical_for_matches(match_ids)
            conflict_ids = CanonicalResultRepository(self.session).conflict_match_ids(match_ids)

            # Count matches where DB sources disagree on result_code.
            # These are excluded from canonical, so result_code=None in details_json.
            rows_with_conflict += len(conflict_ids)

            if not include_partial and not score.is_complete:
                continue

            for detail in details:
                mid = detail.get("match_id")
                if not mid or detail.get("result_code") is None:
                    continue
                if mid not in canonical_map:
                    continue
                if detail.get("recommended_outcome") is None:
                    continue

                total_rows += 1
                hit = detail.get("hit")
                brier = detail.get("brier_score")
                if hit is not None:
                    rows_with_result += 1
                    total_hits += int(hit)
                if brier is not None:
                    brier_accum.append(float(brier))
                if detail.get("ticket_modes"):
                    rows_with_ticket_info += 1
                band = detail.get("confidence_band") or "low"
                if band in band_stats and hit is not None:
                    band_stats[band]["total"] += 1
                    band_stats[band]["hits"] += int(hit)

        return AdaptiveDatasetSummary(
            total_slates_scored=total_slates_scored,
            total_slates_complete=total_slates_complete,
            total_rows=total_rows,
            rows_with_canonical_result=rows_with_result,
            rows_with_conflict=rows_with_conflict,
            rows_with_ticket_info=rows_with_ticket_info,
            hit_rate=(
                round(total_hits / rows_with_result, 4) if rows_with_result > 0 else None
            ),
            brier_score_avg=(
                round(sum(brier_accum) / len(brier_accum), 4) if brier_accum else None
            ),
            by_confidence_band={
                band: ConfidenceBandDatasetStats(
                    total=v["total"],
                    hits=v["hits"],
                    hit_rate=(
                        round(v["hits"] / v["total"], 4) if v["total"] > 0 else None
                    ),
                )
                for band, v in band_stats.items()
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_row(
        score: Any,
        detail: dict[str, Any],
        ticket_modes: dict[str, Any],
        blocked_reason: str | None,
    ) -> AdaptiveDatasetRow:
        def _picks(mode: str) -> list[str] | None:
            m = ticket_modes.get(mode)
            return m.get("picks") if m else None

        def _hit(mode: str) -> bool | None:
            m = ticket_modes.get(mode)
            return m.get("hit") if m else None

        return AdaptiveDatasetRow(
            slate_id=score.slate_id,
            draw_code=score.draw_code,
            week_type=score.week_type,
            composition_hash=score.composition_hash,
            slate_version=score.slate_version,
            match_id=detail["match_id"],
            position=detail.get("position"),
            home_team=detail.get("home_team_name", ""),
            away_team=detail.get("away_team_name", ""),
            competition=detail.get("competition_name", ""),
            prob_home=detail.get("home_probability"),
            prob_draw=detail.get("draw_probability"),
            prob_away=detail.get("away_probability"),
            recommended_outcome=detail.get("recommended_outcome"),
            confidence_band=detail.get("confidence_band"),
            blocked_reason=blocked_reason,
            actual_result=detail["result_code"],
            home_goals=detail.get("home_goals"),
            away_goals=detail.get("away_goals"),
            hit=detail.get("hit"),
            brier_score=detail.get("brier_score"),
            result_is_canonical=True,
            ticket_pick_simple=_picks("simple"),
            ticket_pick_doubles=_picks("doubles"),
            ticket_pick_full=_picks("full"),
            ticket_hit_simple=_hit("simple"),
            ticket_hit_doubles=_hit("doubles"),
            ticket_hit_full=_hit("full"),
        )

    def _blocked_reasons(
        self,
        slate_id: str,
        composition_hash: str,
        match_ids: list[str],
    ) -> dict[str, str | None]:
        """Fetch blocked_reason from the latest audit prediction per match."""
        if not match_ids:
            return {}
        subq = (
            select(
                PredictionModel.id.label("prediction_id"),
                PredictionModel.match_id,
                func.row_number()
                .over(
                    partition_by=PredictionModel.match_id,
                    order_by=(PredictionModel.generated_at.desc(), PredictionModel.id.desc()),
                )
                .label("rn"),
            )
            .where(
                PredictionModel.slate_id == slate_id,
                PredictionModel.composition_hash == composition_hash,
                PredictionModel.match_id.in_(match_ids),
            )
            .subquery()
        )
        stmt = (
            select(PredictionModel.match_id, PredictionModel.blocked_reason)
            .join(subq, PredictionModel.id == subq.c.prediction_id)
            .where(
                PredictionModel.slate_id == slate_id,
                PredictionModel.composition_hash == composition_hash,
                subq.c.rn == 1,
            )
        )
        return {mid: reason for mid, reason in self.session.execute(stmt)}
