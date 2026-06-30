"""R7.0 — Learning slate scoring (read-only post-jornada comparison).

Compares, for a completed/comparable slate, the model's predictions against the
canonical official results and produces a learning-grade scorecard: hit-rate,
top-1/top-2 coverage, Brier and log-loss (over the decision probabilities), plus
a per-position breakdown carrying the error type, guardrail status and whether
Money Mode had blocked the slate.

It compares predictions vs decision/display/effective probabilities and the
real result. It writes nothing — this is the comparison layer of the learning
loop, not a trainer.
"""
from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel, ProgolSlateModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.services.learning_error_attribution_service import classify_position, guardrail_status
from app.services.slate_classification_service import classify_slate

_SIGN_FROM_CODE = {"1": "L", "X": "E", "2": "V"}
_CODE_FROM_SIGN = {"L": "1", "E": "X", "V": "2"}
_LOGLOSS_EPS = 1e-12


def _sign(code: str | None) -> str | None:
    if code is None:
        return None
    return _SIGN_FROM_CODE.get(str(code), None)


def _decision_probs(pred: PredictionModel) -> dict[str, float]:
    return {
        "L": float(pred.home_probability),
        "E": float(pred.draw_probability),
        "V": float(pred.away_probability),
    }


def _audit_probs(pred: PredictionModel, key: str) -> dict[str, float] | None:
    if not pred.sanity_audit_json:
        return None
    try:
        audit = json.loads(pred.sanity_audit_json)
    except (json.JSONDecodeError, TypeError):
        return None
    raw = audit.get(key)
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for sign in ("L", "E", "V"):
        if sign in raw:
            out[sign] = float(raw[sign])
    return out or None


def _audit_value(pred: PredictionModel, key: str) -> Any:
    if not pred.sanity_audit_json:
        return None
    try:
        audit = json.loads(pred.sanity_audit_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return audit.get(key)


class LearningSlateScoringService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def score_slate(self, slate: ProgolSlateModel) -> dict[str, Any]:
        slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
        match_ids = [sm.match_id for sm in slate_matches]
        predictions = self._latest_predictions(slate, match_ids)
        canonical = CanonicalResultRepository(self.session).get_with_conflict_info(match_ids)
        money_blocked = self._money_mode_blocked(slate)
        reality = classify_slate(self.session, slate)

        by_position: list[dict[str, Any]] = []
        hits = 0
        scored = 0
        top1_hits = 0
        top2_covered = 0
        brier_terms: list[float] = []
        logloss_terms: list[float] = []

        for sm in slate_matches:
            pred = predictions.get(sm.match_id)
            cr = canonical.get(sm.match_id)
            conflict = bool(cr and cr.is_conflicting)
            actual_sign = None if cr is None or conflict else _sign(cr.result.result_code)
            pred_sign = _sign(pred.recommended_outcome) if pred is not None else None

            decision = _decision_probs(pred) if pred is not None else None
            effective = _audit_probs(pred, "effective_probabilities") if pred is not None else None
            final_status = _audit_value(pred, "final_status") if pred is not None else None
            evidence_level = _audit_value(pred, "evidence_level") if pred is not None else None

            p_actual = (
                decision.get(actual_sign, 0.0)
                if decision is not None and actual_sign is not None
                else None
            )

            hit: bool | None = None
            if pred_sign is not None and actual_sign is not None:
                scored += 1
                hit = pred_sign == actual_sign
                if hit:
                    hits += 1
                # top-1 = the recommended (argmax decision) sign.
                if decision is not None:
                    top1 = max(decision, key=lambda k: decision[k])
                    if top1 == actual_sign:
                        top1_hits += 1
                    top2 = sorted(decision, key=lambda k: decision[k], reverse=True)[:2]
                    if actual_sign in top2:
                        top2_covered += 1
                    brier_terms.append(self._brier(decision, actual_sign))
                    logloss_terms.append(-math.log(max(decision.get(actual_sign, 0.0), _LOGLOSS_EPS)))

            attribution = classify_position(
                prediction_sign=pred_sign,
                actual_sign=actual_sign,
                decision_probs=decision,
                effective_probs=effective,
                final_status=final_status,
                evidence_level=evidence_level,
                money_blocked=money_blocked,
                has_prediction=pred is not None,
                has_result=actual_sign is not None,
                result_conflict=conflict,
            )

            match = sm.match
            by_position.append(
                {
                    "position": sm.position,
                    "match": f"{match.home_team.name} vs {match.away_team.name}",
                    "prediction": pred_sign,
                    "actual": actual_sign,
                    "hit": hit,
                    "probability_assigned_to_actual": round(p_actual, 4) if p_actual is not None else None,
                    "decision_probabilities": {k: round(v, 4) for k, v in decision.items()} if decision else None,
                    "effective_probabilities": {k: round(v, 4) for k, v in effective.items()} if effective else None,
                    "error_type": attribution["error_type"],
                    "reason": attribution["reason"],
                    "should_have_blocked": attribution["should_have_blocked"],
                    "money_mode_label": attribution["money_mode_label"],
                    "money_mode_decision_correct": attribution["money_mode_decision_correct"],
                    "guardrail_status": guardrail_status(final_status),
                    "final_status": final_status,
                    "was_money_mode_blocked": money_blocked,
                    "result_conflict": conflict,
                }
            )

        total = len(slate_matches)
        canonical_full = sum(1 for sm in slate_matches if sm.match_id in canonical and not canonical[sm.match_id].is_conflicting)
        comparable = (
            total > 0
            and canonical_full == total
            and scored == total
            and reality.comparable_with_results
        )

        score = {
            "hits": hits,
            "total": scored,
            "hit_rate": round(hits / scored, 4) if scored else None,
            "top1_hits": top1_hits,
            "top2_covered": top2_covered,
            "brier": round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None,
            "logloss": round(sum(logloss_terms) / len(logloss_terms), 4) if logloss_terms else None,
        }

        return {
            "mode": "learning_slate_scoring",
            "draw_code": slate.draw_code,
            "slate_id": slate.id,
            "week_type": slate.week_type,
            "comparable": comparable,
            "comparable_lineage": reality.comparable_with_results,
            "classification": reality.classification.value,
            "match_count": total,
            "canonical_results": canonical_full,
            "money_mode_blocked": money_blocked,
            "score": score,
            "by_position": by_position,
            "write_safety": {"writes_performed": False, "snapshots_created": False},
        }

    @staticmethod
    def _brier(probs: dict[str, float], actual_sign: str) -> float:
        return round(
            sum((probs.get(s, 0.0) - (1.0 if s == actual_sign else 0.0)) ** 2 for s in ("L", "E", "V")),
            6,
        )

    def _latest_predictions(
        self, slate: ProgolSlateModel, match_ids: list[str]
    ) -> dict[str, PredictionModel]:
        """Latest prediction per match, preferring this slate + composition_hash.

        Falls back to any prediction for the match so a completed fixture is
        never reported as prediction-less when one exists.
        """
        if not match_ids:
            return {}
        rows = self.session.scalars(
            select(PredictionModel)
            .where(PredictionModel.match_id.in_(match_ids))
            .order_by(
                (PredictionModel.slate_id == slate.id).desc(),
                (PredictionModel.composition_hash == slate.composition_hash).desc(),
                PredictionModel.generated_at.desc(),
                PredictionModel.id.desc(),
            )
        ).all()
        latest: dict[str, PredictionModel] = {}
        for pred in rows:
            latest.setdefault(pred.match_id, pred)
        return latest

    def _money_mode_blocked(self, slate: ProgolSlateModel) -> bool:
        try:
            from app.services.money_mode_service import build_money_mode

            report = build_money_mode(self.session, slate)
            return report.get("decision", {}).get("status") == "NO_JUGAR"
        except Exception:  # pragma: no cover - money mode is best-effort context
            return False


def score_slate_for_draw_code(session: Session, draw_code: str) -> dict[str, Any] | None:
    from app.repositories.slate_repository import SlateRepository

    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        return None
    return LearningSlateScoringService(session).score_slate(slate)


def score_comparable_slates(session: Session) -> dict[str, Any]:
    """Score every comparable slate (full canonical coverage + official lineage)."""
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    service = SlateService(SlateRepository(session))
    slates = service.list_slates(include_closed=True)
    out: list[dict[str, Any]] = []
    for slate in slates:
        report = LearningSlateScoringService(session).score_slate(slate)
        out.append(report)
    comparable = [r for r in out if r["comparable"]]
    return {
        "mode": "learning_slate_scoring_all",
        "slate_count": len(out),
        "comparable_count": len(comparable),
        "comparable_slates": [r["draw_code"] for r in comparable],
        "slates": out,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
