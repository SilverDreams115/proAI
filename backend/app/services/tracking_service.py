"""Phase A — Seguimiento (tracking) builder.

Composes the EXISTING result/prediction machinery into the per-slate
tracking + comparison view, without adding a parallel source of truth:

  * :class:`LiveResultsService` — per-match ORIGINAL prediction hit + real
    (canonical / live) result + completed/live/pending counts.
  * :class:`PredictionService` — live sanity recompute for the raw vs
    decision probability split + ticket strategy (these are not persisted
    reliably, so they are re-derived the same way the "Predicción actual"
    tab does).
  * :class:`CanonicalResultRepository` — conflicting-source detection.
  * ``classify_slate`` — whether the slate is comparable with results
    (friendly / demo / unverified slates never feed training).

Strictly read-only: never writes, trains, promotes, or fabricates a
result. Pending matches never become learning-ready.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.repositories.entity_repository import EntityRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.training_repository import TrainingRepository
from app.services.live_results_service import LiveResultsService
from app.services.model_training_service import ModelTrainingService
from app.services.prediction_service import PredictionService
from app.services.slate_classification_service import classify_slate
from app.services.slate_service import SlateService

# Progol result codes -> displayed L/E/V (local / empate / visitante).
_LETTER = {"1": "L", "X": "E", "2": "V"}
_LEV = ("L", "E", "V")


class TrackingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build_tracking(self, slate: Any) -> dict[str, Any]:
        # Captured BEFORE any recompute so freshly-persisted audit rows can
        # never be mistaken for the original (historical) prediction.
        request_start = datetime.now(timezone.utc)

        live = LiveResultsService(self.session)
        results = live.build_live_results(slate)
        match_rows = results["matches"]
        match_ids = [m["match_id"] for m in match_rows]

        # Historical sanity-audit trace (raw/decision as persisted at decision
        # time). Read first; only recompute when at least one match lacks one.
        historical_audits = self._historical_audits(slate, match_ids, request_start)
        needs_recompute = any(
            m["predicted_outcome"] is not None and m["match_id"] not in historical_audits
            for m in match_rows
        )
        preds = self._slate_predictions(slate) if needs_recompute else {}
        canonical_repo = CanonicalResultRepository(self.session)
        conflict_ids = canonical_repo.conflict_match_ids(match_ids)
        # Matches with a CANONICAL, scored result (match_results, goals NOT NULL).
        # Progol's official acta is sign-only and never lands here, so a final
        # sign-only result is "result present but not canonical/scored".
        canonical_ids = set(canonical_repo.get_canonical_for_matches(match_ids).keys())
        comparable = classify_slate(self.session, slate).comparable_with_results

        matches: list[dict[str, Any]] = []
        hits = misses = scored = ready = waiting = excluded = sign_only = 0

        for m in match_rows:
            mid = m["match_id"]
            pred = preds.get(mid)
            is_final = bool(m["is_final"])
            is_live = bool(m["is_live"])
            result_code = m["result_code"]
            # Original pick stays faithful to the STORED prediction; the live
            # recompute only supplies the raw/decision split + ticket strategy.
            original_pick = _LETTER.get(m["predicted_outcome"]) if m["predicted_outcome"] else None
            actual_result = _LETTER.get(result_code) if result_code else None

            if is_final:
                match_status = "finished"
            elif is_live:
                match_status = "live"
            else:
                match_status = "pending"

            if not is_final:
                prediction_status = "pending"
            else:
                prediction_status = "hit" if m["prediction_hit"] else "miss"

            # Conflict is checked FIRST: a match whose sources disagree has
            # results (so it is not "pending") but no canonical truth, so it
            # can never be ready — it is excluded regardless of final status.
            exclusion_reason: str | None = None
            if mid in conflict_ids:
                learning_status = "excluded"
                exclusion_reason = "conflicting_results"
            elif not is_final:
                learning_status = "waiting_result"
            elif not comparable:
                learning_status = "excluded"
                exclusion_reason = "non_comparable_slate"
            elif m["predicted_outcome"] is None:
                learning_status = "excluded"
                exclusion_reason = "missing_prediction"
            elif mid not in canonical_ids:
                # Final result exists (sign known, tracking shows hit/miss) but
                # there is no canonical SCORED result, so the adaptive dataset
                # cannot use it. Distinct from a hard exclusion or pending.
                learning_status = "sign_only"
                exclusion_reason = "sign_only_no_canonical_score"
            else:
                learning_status = "ready"
            # Anything not "ready"/"waiting_result" cannot feed training.
            excluded_from_training = learning_status not in {"ready", "waiting_result"}

            if is_final and m["predicted_outcome"] is not None:
                scored += 1
                if prediction_status == "hit":
                    hits += 1
                else:
                    misses += 1
            if learning_status == "ready":
                ready += 1
            elif learning_status == "waiting_result":
                waiting += 1
            elif learning_status == "sign_only":
                sign_only += 1
            else:
                excluded += 1

            # Probability provenance — observability only. hit/miss (above) and
            # learning_status (below) NEVER read these recomputed vectors.
            audit = historical_audits.get(mid)
            stored_decision = self._stored_vector(m)
            raw_probabilities: dict[str, float] | None
            decision_probabilities: dict[str, float] | None
            ticket_strategy: str | None
            if audit is not None:
                probability_source = "persisted_sanity_audit"
                raw_probabilities = audit["raw"]
                decision_probabilities = audit["decision"]
                raw_is_historical = True
                decision_is_historical = True
                ticket_strategy = audit.get("ticket_strategy") or (
                    pred.ticket_strategy if pred is not None else None
                )
            elif pred is not None:
                probability_source = "recomputed_current_sanity"
                raw_probabilities = dict(pred.raw_probabilities)
                decision_probabilities = dict(pred.decision_probabilities)
                raw_is_historical = False
                decision_is_historical = False
                ticket_strategy = pred.ticket_strategy
            else:
                probability_source = "decision_only"
                raw_probabilities = None
                decision_probabilities = stored_decision
                raw_is_historical = False
                decision_is_historical = stored_decision is not None
                ticket_strategy = None

            matches.append(
                {
                    "position": m["position"],
                    "home": m["home_team_name"],
                    "away": m["away_team_name"],
                    "competition": m["competition_name"],
                    "original_pick": original_pick,
                    "raw_probabilities": raw_probabilities,
                    "decision_probabilities": decision_probabilities,
                    "ticket_strategy": ticket_strategy,
                    "probability_source": probability_source,
                    "raw_probabilities_is_historical": raw_is_historical,
                    "decision_probabilities_is_historical": decision_is_historical,
                    "actual_result": actual_result,
                    "home_score": m["home_goals"],
                    "away_score": m["away_goals"],
                    "match_status": match_status,
                    "prediction_status": prediction_status,
                    "learning_status": learning_status,
                    "excluded_from_training": excluded_from_training,
                    "exclusion_reason": exclusion_reason,
                }
            )

        total = results["match_count"]
        finished = results["completed_count"]
        live_n = results["live_count"]
        pending = results["pending_count"]
        accuracy = round(hits / scored, 3) if scored > 0 else None

        if results["is_complete"]:
            status = "complete"
        elif live_n > 0:
            status = "live"
        elif SlateService(SlateRepository(self.session)).is_closed(slate):
            status = "closed"
        else:
            status = "open"

        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "status": status,
            "total_matches": total,
            "finished_matches": finished,
            "live_matches": live_n,
            "pending_matches": pending,
            "scored_matches": scored,
            "hits": hits,
            "misses": misses,
            "accuracy": accuracy,
            "learning_rows_ready": ready,
            "learning_rows_pending": waiting,
            "learning_rows_excluded": excluded,
            "learning_rows_sign_only": sign_only,
            "has_conflicts": len(conflict_ids) > 0,
            "comparable_with_results": comparable,
            "last_result_update": results["last_updated_at"],
            "matches": matches,
        }

    def _historical_audits(
        self, slate: Any, match_ids: list[str], request_start: datetime
    ) -> dict[str, dict[str, Any]]:
        """Latest persisted sanity-audit trace per match, bounded so it only
        ever returns the ORIGINAL (decision-time) prediction.

        The bound is the slate's cierre when it has already passed, otherwise
        the moment this request started. Either way, audit rows written by the
        recompute that runs LATER in this same request are excluded, so a
        recompute can never masquerade as a historical record.
        """
        comp_hash = slate.composition_hash or ""
        if not comp_hash or not match_ids:
            return {}
        closes_at = slate.registration_closes_at
        if closes_at is not None and closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        cutoff = closes_at if (closes_at is not None and closes_at <= request_start) else request_start
        stmt = (
            select(
                PredictionModel.match_id,
                PredictionModel.sanity_audit_json,
            )
            .where(
                PredictionModel.slate_id == slate.id,
                PredictionModel.composition_hash == comp_hash,
                PredictionModel.match_id.in_(match_ids),
                PredictionModel.sanity_audit_json.is_not(None),
                PredictionModel.sanity_audit_json != "",
                PredictionModel.generated_at <= cutoff,
            )
            .order_by(PredictionModel.generated_at.desc(), PredictionModel.id.desc())
        )
        out: dict[str, dict[str, Any]] = {}
        for mid, audit_json in self.session.execute(stmt):
            if mid in out:
                continue  # desc order -> first seen is the latest
            parsed = self._parse_audit(audit_json)
            if parsed is not None:
                out[mid] = parsed
        return out

    @staticmethod
    def _parse_audit(audit_json: str | None) -> dict[str, Any] | None:
        """Extract the historical L/E/V raw + decision vectors from a stored
        sanity_audit_json, or None when it is missing/malformed."""
        if not audit_json:
            return None
        try:
            data = json.loads(audit_json)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        raw = data.get("raw_probabilities")
        decision = data.get("decision_probabilities") or data.get("display_probabilities")
        if not isinstance(raw, dict) or not isinstance(decision, dict):
            return None
        if not all(k in raw for k in _LEV) or not all(k in decision for k in _LEV):
            return None
        return {
            "raw": {k: float(raw[k]) for k in _LEV},
            "decision": {k: float(decision[k]) for k in _LEV},
            "ticket_strategy": data.get("ticket_strategy"),
        }

    @staticmethod
    def _stored_vector(m: dict[str, Any]) -> dict[str, float] | None:
        """L/E/V decision vector from the stored prediction probabilities,
        or None when no prediction is linked to the match."""
        home = m.get("home_probability")
        draw = m.get("draw_probability")
        away = m.get("away_probability")
        if home is None or draw is None or away is None:
            return None
        return {"L": float(home), "E": float(draw), "V": float(away)}

    def _slate_predictions(self, slate: Any) -> dict[str, Any]:
        """Live sanity recompute, keyed by match_id (same path as the
        Predicción actual tab). Returns {} defensively on failure so a
        prediction hiccup never breaks the tracking view."""
        try:
            training_service = ModelTrainingService(
                TrainingRepository(self.session),
                EntityRepository(self.session),
                ResultRepository(self.session),
            )
            # persist_audit=False keeps tracking/comparison 100% read-only:
            # recompute the raw/decision/sanity vectors without inserting any
            # audit row (or touching the prediction cache).
            preds = PredictionService(training_service).build_slate_predictions(
                slate, persist_audit=False
            )
            return {p.match_id: p for p in preds}
        except Exception:  # pragma: no cover - defensive; tracking still renders
            return {}
