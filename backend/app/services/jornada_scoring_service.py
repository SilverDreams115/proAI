from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    PredictionModel,
    ProgolJornadaScoreModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)

logger = logging.getLogger(__name__)


class JornadaScoringService:
    """Compute hit-rate and Brier-score metrics for a completed (or partial)
    Progol jornada.

    Uses only predictions that are linked to the slate's current
    composition_hash, so stale predictions from a prior fixture lineup
    never pollute the score. If no valid ticket snapshot exists the
    ticket_hits fields are set to None (unavailable), not zero.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def compute_for_slate(self, slate: ProgolSlateModel) -> ProgolJornadaScoreModel:
        if not slate.composition_hash:
            raise ValueError(
                f"Slate {slate.draw_code!r} (id={slate.id}) has no composition_hash. "
                "Run the startup backfill or upsert the slate to generate a hash."
            )

        slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
        match_ids = [sm.match_id for sm in slate_matches]

        predictions_by_match = self._latest_predictions(slate.id, slate.composition_hash, match_ids)
        results_by_match = self._canonical_results(match_ids)
        snapshot = self._valid_snapshot(slate.id, slate.composition_hash)
        ticket_picks = self._parse_ticket_picks(snapshot)
        has_snapshot = snapshot is not None

        total_matches = len(slate_matches)
        matches_with_results = 0
        simple_hits = 0
        brier_scores: list[float] = []
        band_hits: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "blocked": 0}
        band_total: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "blocked": 0}
        ticket_simple_hits = 0
        ticket_simple_evaluable = 0
        details: list[dict[str, Any]] = []

        for sm in slate_matches:
            match = sm.match
            pred = predictions_by_match.get(sm.match_id)
            result = results_by_match.get(sm.match_id)
            t_picks = ticket_picks.get(sm.match_id)

            has_result = result is not None
            if has_result:
                matches_with_results += 1

            hit: bool | None = None
            brier_score: float | None = None
            if has_result and pred is not None:
                hit = pred.recommended_outcome == result.result_code
                if hit:
                    simple_hits += 1
                brier_score = self._brier_score(
                    pred.home_probability,
                    pred.draw_probability,
                    pred.away_probability,
                    result.result_code,
                )
                brier_scores.append(brier_score)
                band = pred.confidence_band or "low"
                if band in band_total:
                    band_total[band] += 1
                    if hit:
                        band_hits[band] += 1

            ticket_mode_detail: dict[str, Any] | None = None
            if t_picks is not None:
                ticket_mode_detail = {}
                for mode, mode_data in t_picks.items():
                    mode_hit: bool | None = None
                    if has_result:
                        mode_hit = result.result_code in mode_data["picks"]
                    ticket_mode_detail[mode] = {
                        "pick_type": mode_data["pick_type"],
                        "picks": mode_data["picks"],
                        "hit": mode_hit,
                    }
                if has_result:
                    simple_mode = t_picks.get("simple")
                    if simple_mode:
                        ticket_simple_evaluable += 1
                        if result.result_code in simple_mode["picks"]:
                            ticket_simple_hits += 1

            details.append(
                {
                    "match_id": sm.match_id,
                    "position": sm.position,
                    "home_team_name": match.home_team.name,
                    "away_team_name": match.away_team.name,
                    "competition_name": match.competition.name,
                    "result_code": result.result_code if result else None,
                    "home_goals": result.home_goals if result else None,
                    "away_goals": result.away_goals if result else None,
                    "recommended_outcome": pred.recommended_outcome if pred else None,
                    "confidence_band": pred.confidence_band if pred else None,
                    "home_probability": pred.home_probability if pred else None,
                    "draw_probability": pred.draw_probability if pred else None,
                    "away_probability": pred.away_probability if pred else None,
                    "generated_at": pred.generated_at.isoformat() if pred else None,
                    "hit": hit,
                    "brier_score": brier_score,
                    "ticket_modes": ticket_mode_detail,
                }
            )

        simple_hit_rate = (
            round(simple_hits / matches_with_results, 4) if matches_with_results > 0 else None
        )
        ticket_hits: int | None = ticket_simple_hits if has_snapshot else None
        ticket_hit_rate: float | None = None
        if has_snapshot and ticket_simple_evaluable > 0:
            ticket_hit_rate = round(ticket_simple_hits / ticket_simple_evaluable, 4)
        brier_score_avg = (
            round(sum(brier_scores) / len(brier_scores), 4) if brier_scores else None
        )
        is_complete = matches_with_results == total_matches and total_matches > 0

        logger.info(
            "jornada_score_computed",
            extra={
                "event": "jornada_score_computed",
                "draw_code": slate.draw_code,
                "slate_id": slate.id,
                "composition_hash": slate.composition_hash[:16] + "..." if slate.composition_hash else None,
                "total_matches": total_matches,
                "matches_with_results": matches_with_results,
                "simple_hits": simple_hits,
                "is_complete": is_complete,
            },
        )

        return ProgolJornadaScoreModel(
            slate_id=slate.id,
            draw_code=slate.draw_code,
            week_type=slate.week_type,
            composition_hash=slate.composition_hash,
            slate_version=getattr(slate, "slate_version", None),
            total_matches=total_matches,
            matches_with_results=matches_with_results,
            simple_hits=simple_hits,
            simple_hit_rate=simple_hit_rate,
            ticket_hits=ticket_hits,
            ticket_hit_rate=ticket_hit_rate,
            brier_score_avg=brier_score_avg,
            high_confidence_hits=band_hits["high"],
            high_confidence_total=band_total["high"],
            medium_confidence_hits=band_hits["medium"],
            medium_confidence_total=band_total["medium"],
            low_confidence_hits=band_hits["low"],
            low_confidence_total=band_total["low"],
            blocked_hits=band_hits["blocked"],
            blocked_total=band_total["blocked"],
            details_json=json.dumps(details, default=str),
            is_complete=is_complete,
        )

    @staticmethod
    def _brier_score(p_home: float, p_draw: float, p_away: float, result_code: str) -> float:
        """Multi-class Brier score for a single match.

        Range is [0, 2]: 0 = perfect prediction, 2 = worst possible.
        """
        i_home = 1.0 if result_code == "1" else 0.0
        i_draw = 1.0 if result_code == "X" else 0.0
        i_away = 1.0 if result_code == "2" else 0.0
        return round(
            (p_home - i_home) ** 2 + (p_draw - i_draw) ** 2 + (p_away - i_away) ** 2,
            4,
        )

    def _latest_predictions(
        self,
        slate_id: str,
        composition_hash: str,
        match_ids: list[str],
    ) -> dict[str, PredictionModel]:
        if not match_ids:
            return {}
        subq = (
            select(
                PredictionModel.match_id,
                func.max(PredictionModel.generated_at).label("max_gen"),
            )
            .where(
                PredictionModel.slate_id == slate_id,
                PredictionModel.composition_hash == composition_hash,
                PredictionModel.match_id.in_(match_ids),
            )
            .group_by(PredictionModel.match_id)
            .subquery()
        )
        stmt = (
            select(PredictionModel)
            .join(
                subq,
                (PredictionModel.match_id == subq.c.match_id)
                & (PredictionModel.generated_at == subq.c.max_gen),
            )
            .where(
                PredictionModel.slate_id == slate_id,
                PredictionModel.composition_hash == composition_hash,
            )
        )
        return {p.match_id: p for p in self.session.scalars(stmt)}

    def _canonical_results(self, match_ids: list[str]) -> dict[str, Any]:
        """Return canonical (non-conflicting) results keyed by match_id.

        Matches whose result_codes disagree across sources are excluded so
        the scorer never silently picks the wrong one.
        """
        from app.repositories.canonical_result_repository import CanonicalResultRepository

        return CanonicalResultRepository(self.session).get_canonical_for_matches(match_ids)

    def _valid_snapshot(
        self, slate_id: str, composition_hash: str
    ) -> TicketRecommendationSnapshotModel | None:
        stmt = (
            select(TicketRecommendationSnapshotModel)
            .where(
                TicketRecommendationSnapshotModel.slate_id == slate_id,
                TicketRecommendationSnapshotModel.is_valid.is_(True),
                TicketRecommendationSnapshotModel.composition_hash == composition_hash,
            )
            .order_by(TicketRecommendationSnapshotModel.generated_at.desc())
        )
        return self.session.scalar(stmt)

    @staticmethod
    def _parse_ticket_picks(
        snapshot: TicketRecommendationSnapshotModel | None,
    ) -> dict[str, dict[str, Any]]:
        if snapshot is None:
            return {}
        try:
            payload = json.loads(snapshot.payload_json)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}
        picks: dict[str, dict[str, Any]] = {}
        for rec in payload.get("recommendations", []):
            match_id = rec.get("match_id")
            if not match_id:
                continue
            decisions = rec.get("decisions", {})
            picks[match_id] = {
                mode: {
                    "pick_type": dec.get("pick_type", "fixed"),
                    "picks": [str(p) for p in dec.get("picks", [])],
                }
                for mode, dec in decisions.items()
                if isinstance(dec, dict)
            }
        return picks
