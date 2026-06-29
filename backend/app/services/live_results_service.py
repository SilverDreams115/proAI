"""Live / partial scoring + per-match tracking for a Progol slate.

Combines three inputs without ever mutating predictions or fabricating
results:

* latest predictions for the slate's current composition_hash,
* the valid ticket snapshot's per-mode picks (simple / doubles / full),
* normalized live/final results from :class:`LiveResultService`.

Produces two views:

* ``build_live_results`` — per-match prediction + result + hit + draw
  coverage + status, plus completed/live/pending counts.
* ``build_live_score`` — partial/live scoring with current hits, the
  min/max still reachable, draw deltas, and ``is_complete`` (true only
  when every match has a FINAL result). It never persists a final score
  on its own; persistence stays with JornadaScoringService.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.domain.entities import MatchResultStatus
from app.models.tables import ProgolSlateModel
from app.services.jornada_scoring_service import JornadaScoringService
from app.services.live_result_service import LiveResultService, NormalizedMatchResult
from app.services.prediction_probabilities import (
    draw_calibration_info,
    raw_probabilities,
    visible_probabilities,
)
from app.services.ticket_recommendation_service import TicketRecommendationService

_LIVE_THRESHOLD = TicketRecommendationService.LIVE_DRAW_THRESHOLD
_STRONG_THRESHOLD = TicketRecommendationService.STRONG_DRAW_THRESHOLD


@dataclass
class _MatchEval:
    detail: dict[str, Any]
    result: NormalizedMatchResult | None
    pred: Any
    p_draw: float | None
    # Visible/decision (home, draw, away) vector used for Brier — kept on
    # the eval so build_live_score scores the same numbers the UI shows.
    visible: tuple[float, float, float] | None = None


class LiveResultsService:
    def __init__(self, session: Any) -> None:
        self.session = session
        self._scorer = JornadaScoringService(session)
        self._live = LiveResultService(session)

    # ---- public views --------------------------------------------------

    def build_live_results(self, slate: ProgolSlateModel) -> dict[str, Any]:
        evals, last_updated = self._evaluate(slate)
        completed = sum(1 for e in evals if e.result is not None and e.result.is_final)
        live = sum(1 for e in evals if e.result is not None and e.result.is_live)
        pending = len(evals) - completed - live
        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "is_archived": slate.is_archived,
            "composition_hash": slate.composition_hash,
            "match_count": len(evals),
            "completed_count": completed,
            "live_count": live,
            "pending_count": pending,
            "is_complete": len(evals) > 0 and completed == len(evals),
            "last_updated_at": last_updated,
            "matches": [e.detail for e in evals],
        }

    def build_live_score(self, slate: ProgolSlateModel) -> dict[str, Any]:
        evals, last_updated = self._evaluate(slate)
        total = len(evals)
        finals = [e for e in evals if e.result is not None and e.result.is_final]
        live_n = sum(1 for e in evals if e.result is not None and e.result.is_live)
        evaluated = len(finals)
        pending = total - evaluated - live_n

        hits = {"simple": 0, "doubles": 0, "full": 0}
        remaining = {"simple": 0, "doubles": 0, "full": 0}
        guaranteed_remaining = {"simple": 0, "doubles": 0, "full": 0}
        brier_scores: list[float] = []
        empates_reales = 0
        empates_esperados_eval = 0.0
        empates_esperados_total = 0.0

        for e in evals:
            modes = e.detail.get("ticket_modes") or {}
            if e.p_draw is not None:
                empates_esperados_total += e.p_draw
            is_final = e.result is not None and e.result.is_final
            if is_final:
                assert e.result is not None
                code = e.result.result_code
                if code == "X":
                    empates_reales += 1
                if e.p_draw is not None:
                    empates_esperados_eval += e.p_draw
                for mode in hits:
                    picks = (modes.get(mode) or {}).get("picks") or []
                    if code is not None and code in picks:
                        hits[mode] += 1
                if e.visible is not None and code is not None:
                    brier_scores.append(
                        self._scorer._brier_score(
                            e.visible[0],
                            e.visible[1],
                            e.visible[2],
                            code,
                        )
                    )
            else:
                # Non-final match: each mode can still land (optimistic),
                # and is guaranteed only when it already covers all three.
                for mode in remaining:
                    picks = (modes.get(mode) or {}).get("picks") or []
                    if picks:
                        remaining[mode] += 1
                    if len(set(picks)) >= 3:
                        guaranteed_remaining[mode] += 1

        current_hit_rate = round(hits["simple"] / evaluated, 4) if evaluated > 0 else None
        brier_partial = (
            round(sum(brier_scores) / len(brier_scores), 4) if brier_scores else None
        )
        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "total_matches": total,
            "evaluated_matches": evaluated,
            "live_matches": live_n,
            "pending_matches": pending,
            "simple_hits": hits["simple"],
            "doubles_hits": hits["doubles"],
            "full_hits": hits["full"],
            "simple_possible_remaining": remaining["simple"],
            "doubles_possible_remaining": remaining["doubles"],
            "full_possible_remaining": remaining["full"],
            "current_hit_rate": current_hit_rate,
            # Headline min/max reachable for the model's single (simple) pick.
            "max_possible_hits": hits["simple"] + remaining["simple"],
            "min_possible_hits": hits["simple"] + guaranteed_remaining["simple"],
            "empates_reales_hasta_ahora": empates_reales,
            "empates_esperados": round(empates_esperados_total, 4),
            "empates_esperados_evaluados": round(empates_esperados_eval, 4),
            "draw_delta_partial": round(empates_reales - empates_esperados_eval, 4),
            "brier_partial": brier_partial,
            "is_complete": total > 0 and evaluated == total,
            "last_updated_at": last_updated,
        }

    def build_result_comparison(self, slate: ProgolSlateModel) -> dict[str, Any]:
        """Postmortem: original pre-close prediction vs the real result.

        Always evaluates against the ORIGINAL snapshot (the ticket as it
        stood at/before cierre) — never a post-result refresh. Adds a
        per-match diagnosis so the user reads "what failed" at a glance.
        """
        from app.services.slate_classification_service import classify_slate

        reality = classify_slate(self.session, slate)
        snapshot = self._original_snapshot(slate)
        evals, last_updated = self._evaluate(slate, snapshot=snapshot)
        score = self.build_live_score(slate)

        matches = []
        for e in evals:
            d = dict(e.detail)
            d["diagnosis"] = self._diagnose(d)
            matches.append(d)

        completed = sum(1 for e in evals if e.result is not None and e.result.is_final)
        live = sum(1 for e in evals if e.result is not None and e.result.is_live)
        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "is_archived": slate.is_archived,
            "composition_hash": slate.composition_hash,
            "classification": reality.classification.value,
            "comparable": reality.comparable_with_results,
            "classification_reasons": reality.reasons,
            "competitions": reality.competitions,
            "source_name": reality.source_name,
            "source_url": reality.source_url,
            "match_count": len(evals),
            "completed_count": completed,
            "live_count": live,
            "pending_count": len(evals) - completed - live,
            "is_complete": score["is_complete"],
            "results_ingested": completed + live > 0,
            "last_updated_at": last_updated,
            "original_snapshot": {
                "snapshot_id": snapshot.id if snapshot is not None else None,
                "generated_at": snapshot.generated_at if snapshot is not None else None,
                "composition_hash": snapshot.composition_hash if snapshot is not None else None,
                "model_version": snapshot.model_version if snapshot is not None else None,
            },
            "score": score,
            "matches": matches,
        }

    def _original_snapshot(self, slate: ProgolSlateModel) -> Any | None:
        """Latest VALID snapshot generated at/before cierre for the slate's
        composition_hash — i.e. the original ticket, never a later refresh.
        Falls back to the latest valid snapshot when no cierre is recorded.
        """
        comp_hash = slate.composition_hash
        if not comp_hash:
            return None
        from app.models.tables import TicketRecommendationSnapshotModel as _Snap

        stmt = (
            select(_Snap)
            .where(
                _Snap.slate_id == slate.id,
                _Snap.is_valid.is_(True),
                _Snap.composition_hash == comp_hash,
            )
            .order_by(_Snap.generated_at.desc())
        )
        if slate.registration_closes_at is not None:
            stmt = stmt.where(_Snap.generated_at <= slate.registration_closes_at)
        snapshot = self.session.scalar(stmt)
        if snapshot is None:
            # No pre-close snapshot recorded → fall back to latest valid.
            return self._scorer._valid_snapshot(slate.id, comp_hash)
        return snapshot

    @staticmethod
    def _diagnose(detail: dict[str, Any]) -> str:
        """One-line read of each match outcome vs the original pick."""
        if detail.get("is_pending"):
            return "pendiente"
        if detail.get("is_live"):
            return "en vivo"
        if detail.get("prediction_hit") is True:
            return "acierto"
        # Final miss — classify by what actually happened.
        code = detail.get("result_code")
        if code == "X":
            return "fallo por empate"
        if code == "1":
            return "fallo (salió local)"
        if code == "2":
            return "fallo (salió visitante)"
        return "fallo"

    # ---- shared evaluation --------------------------------------------

    def _evaluate(
        self, slate: ProgolSlateModel, *, snapshot: Any | None = "__auto__"
    ) -> tuple[list[_MatchEval], datetime | None]:
        slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
        match_ids = [sm.match_id for sm in slate_matches]
        comp_hash = slate.composition_hash or ""
        preds = (
            self._scorer._latest_predictions(slate.id, comp_hash, match_ids)
            if comp_hash
            else {}
        )
        if snapshot == "__auto__":
            snapshot = (
                self._scorer._valid_snapshot(slate.id, comp_hash) if comp_hash else None
            )
        ticket_picks = self._scorer._parse_ticket_picks(snapshot)
        results = self._live.status_for_matches(match_ids)

        last_updated: datetime | None = None
        evals: list[_MatchEval] = []
        for sm in slate_matches:
            match = sm.match
            pred = preds.get(sm.match_id)
            result = results.get(sm.match_id)
            t_picks = ticket_picks.get(sm.match_id, {})
            if result is not None and result.source_updated_at is not None:
                if last_updated is None or result.source_updated_at > last_updated:
                    last_updated = result.source_updated_at

            # Score + display the calibrated/visible (decision) vector; for
            # legacy closed rows this reads the capped vector from the audit
            # rather than the raw model output stored in the columns.
            visible = visible_probabilities(pred) if pred is not None else None
            raw_vec = raw_probabilities(pred) if pred is not None else None
            draw_cal = draw_calibration_info(pred) if pred is not None else None
            p_draw = visible[1] if visible is not None else None
            code = result.result_code if result is not None else None
            modes_detail, covered = self._ticket_modes(t_picks, code)

            prediction_hit: bool | None = None
            if code is not None and pred is not None:
                prediction_hit = pred.recommended_outcome == code

            draw_was_real = (code == "X") if code is not None else None
            draw_risk = (
                self._draw_risk(visible, covered) if visible is not None else None
            )

            status = result.status if result is not None else MatchResultStatus.SCHEDULED
            evals.append(
                _MatchEval(
                    detail={
                        "match_id": sm.match_id,
                        "position": sm.position,
                        "home_team_name": match.home_team.name,
                        "away_team_name": match.away_team.name,
                        "competition_name": match.competition.name,
                        "kickoff_at": match.kickoff_at,
                        "predicted_outcome": pred.recommended_outcome if pred else None,
                        "confidence_band": pred.confidence_band if pred else None,
                        # Visible/decision vector (scored + displayed).
                        "home_probability": visible[0] if visible else None,
                        "draw_probability": visible[1] if visible else None,
                        "away_probability": visible[2] if visible else None,
                        # Raw model output, surfaced for transparency only.
                        "raw_probabilities": (
                            {"L": raw_vec[0], "E": raw_vec[1], "V": raw_vec[2]}
                            if raw_vec is not None
                            else None
                        ),
                        # Conservative draw-calibration trace (note + before/after).
                        "draw_calibration_applied": bool(draw_cal and draw_cal["applied"]),
                        "draw_calibration_reason": (draw_cal or {}).get("reason"),
                        "pre_draw_calibration_probabilities": (draw_cal or {}).get("pre_probabilities"),
                        "home_goals": result.home_goals if result else None,
                        "away_goals": result.away_goals if result else None,
                        "result_code": code,
                        "minute": result.minute if result else None,
                        "status": status.value,
                        "is_final": bool(result and result.is_final),
                        "is_live": bool(result and result.is_live),
                        "is_pending": result is None or result.is_pending,
                        "source": result.source if result else None,
                        "source_updated_at": result.source_updated_at if result else None,
                        "prediction_hit": prediction_hit,
                        "simple_hit": modes_detail["simple"]["hit"],
                        "doubles_hit": modes_detail["doubles"]["hit"],
                        "full_hit": modes_detail["full"]["hit"],
                        "ticket_modes": modes_detail,
                        "draw_was_real": draw_was_real,
                        "draw_was_covered": covered["simple"] or covered["doubles"] or covered["full"],
                        "draw_risk": draw_risk,
                    },
                    result=result,
                    pred=pred,
                    p_draw=p_draw,
                    visible=visible,
                )
            )
        return evals, last_updated

    @staticmethod
    def _ticket_modes(
        t_picks: dict[str, Any], result_code: str | None
    ) -> tuple[dict[str, Any], dict[str, bool]]:
        modes: dict[str, Any] = {}
        covered = {"simple": False, "doubles": False, "full": False}
        for mode in ("simple", "doubles", "full"):
            data = t_picks.get(mode) or {}
            picks = [str(p) for p in data.get("picks", [])]
            hit: bool | None = (result_code in picks) if result_code is not None else None
            covered[mode] = "X" in picks
            modes[mode] = {
                "pick_type": data.get("pick_type"),
                "picks": picks,
                "hit": hit,
            }
        return modes, covered

    @staticmethod
    def _draw_risk(
        visible: tuple[float, float, float], covered: dict[str, bool]
    ) -> dict[str, Any]:
        p_home, p_draw, p_away = visible
        draw_rank = 1 + sum(1 for p in (p_home, p_away) if p > p_draw)
        return {
            "p_draw": round(p_draw, 4),
            "draw_rank": draw_rank,
            "is_live_draw": p_draw >= _LIVE_THRESHOLD,
            "is_strong_draw": p_draw >= _STRONG_THRESHOLD,
            "covered_simple": covered["simple"],
            "covered_doubles": covered["doubles"],
            "covered_full": covered["full"],
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def finalize_complete_closed_slates(
    session: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Persist a final JornadaScore for closed slates that are all-final.

    Observer entry point for the worker. Read-mostly: it only writes the
    canonical final score (idempotent upsert) for a slate whose every
    match already has a FINAL result and whose saved score is missing or
    still partial. Never fabricates a result, never marks an incomplete
    slate complete, and processes each slate independently by its own
    composition_hash (Weekend / Media Semana never mix).
    """
    from app.repositories.jornada_score_repository import JornadaScoreRepository
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_classification_service import classify_slate
    from app.services.slate_service import SlateService

    now = now or datetime.now(timezone.utc)
    slate_service = SlateService(SlateRepository(session))
    score_repo = JornadaScoreRepository(session)
    scorer = JornadaScoringService(session)
    live = LiveResultsService(session)

    checked = 0
    finalized: list[str] = []
    skipped_non_official: list[str] = []
    for slate in slate_service.list_slates(include_closed=True):
        if not slate.composition_hash or not slate_service.is_closed(slate, now):
            continue
        checked += 1
        # Never persist an OFFICIAL JornadaScore for a demo/unverified slate.
        if not classify_slate(session, slate).comparable_with_results:
            skipped_non_official.append(slate.draw_code)
            continue
        score = live.build_live_score(slate)
        if not score["is_complete"]:
            continue
        existing = score_repo.get_latest_for_slate(slate.id)
        if existing is not None and existing.is_complete:
            continue
        score_repo.upsert_score(scorer.compute_for_slate(slate))
        finalized.append(slate.draw_code)
    return {
        "checked": checked,
        "finalized": finalized,
        "skipped_non_official": skipped_non_official,
    }
