from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.db.session import managed_transaction
from app.domain.entities import Outcome
from app.models.tables import ProgolSlateModel
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.schemas.prediction import MatchPredictionResponse
from app.schemas.prediction import MatchTicketRecommendationResponse
from app.schemas.prediction import TicketCoverageMode
from app.schemas.prediction import TicketDecisionResponse
from app.schemas.prediction import TicketRecommendationResponse
from app.schemas.prediction import TicketValidationResponse
from app.services.coverage import prob_at_least
from app.services.ticket_optimizer import TicketOption, optimize_ticket


class TicketRecommendationService:
    MODEL_VERSION = "ticket-optimizer-v2"
    # Default ticket-coverage target: at least 90% of the slate correct,
    # with at least 80% probability. Operators can tune this per slate
    # via the API in a later iteration.
    DEFAULT_COVERAGE_TARGET_FRACTION = 0.9
    DEFAULT_COVERAGE_TARGET_PROBABILITY = 0.8
    OUTCOME_ORDER = [Outcome.HOME, Outcome.DRAW, Outcome.AWAY]
    MULTIPLE_RULES = {
        "weekend": {"doubles_only_max": 8, "combined_double_max": 2, "combined_triple_max": 4},
        "midweek": {"doubles_only_max": 3, "combined_double_max": 3, "combined_triple_max": 2},
        "revancha": {"doubles_only_max": 3, "combined_double_max": 3, "combined_triple_max": 2},
        "fallback": {"doubles_only_max": 3, "combined_double_max": 2, "combined_triple_max": 2},
    }

    def __init__(self, repository: TicketRecommendationRepository) -> None:
        self.repository = repository

    def build_and_persist(
        self,
        *,
        slate: ProgolSlateModel,
        predictions: list[MatchPredictionResponse],
        feature_payloads_by_match: dict[str, dict[str, Any]],
    ) -> TicketRecommendationResponse:
        generated_at = datetime.now(timezone.utc)
        rule = self._rule_for_slate(slate.week_type, len(predictions))
        profiles = {
            prediction.match_id: self._risk_profile(
                prediction,
                feature_payloads_by_match.get(prediction.match_id, {}),
            )
            for prediction in predictions
        }
        double_ids = self._choose_doubles(predictions, profiles, rule["doubles_only_max"])
        full_double_ids, full_triple_ids = self._choose_full_coverage(predictions, profiles, rule)
        recommendations = [
            self._build_match_recommendation(
                prediction=prediction,
                profile=profiles[prediction.match_id],
                double_ids=double_ids,
                full_double_ids=full_double_ids,
                full_triple_ids=full_triple_ids,
            )
            for prediction in predictions
        ]
        coverage = self._coverage_modes(predictions, recommendations)
        payload: dict[str, Any] = {
            "slate_id": slate.id,
            "generated_at": generated_at.isoformat(),
            "model_version": self.MODEL_VERSION,
            "rules": rule,
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "coverage": [item.model_dump(mode="json") for item in coverage],
        }
        with managed_transaction(self.repository.session):
            snapshot = self.repository.save_snapshot(
                slate_id=slate.id,
                model_version=self.MODEL_VERSION,
                payload=payload,
                composition_hash=getattr(slate, "composition_hash", None),
            )
        return TicketRecommendationResponse(
            slate_id=slate.id,
            snapshot_id=snapshot.id,
            generated_at=snapshot.generated_at,
            model_version=self.MODEL_VERSION,
            rules=rule,
            recommendations=recommendations,
            coverage=coverage,
        )

    def _coverage_modes(
        self,
        predictions: list[MatchPredictionResponse],
        recommendations: list[MatchTicketRecommendationResponse],
    ) -> list[TicketCoverageMode]:
        """Build a coverage projection per ticket mode.

        For each mode we read the decisions back, compute the effective
        hit probability per match (top1 for fixed, top1+top2 for double,
        1.0 for triple), and apply the Poisson Binomial CDF. The result
        tells the operator exactly how likely the boleta is to clear the
        target floor — the answer to 'will at least 8/9 land correctly?'."""
        if not predictions:
            return []
        prob_by_match = {
            prediction.match_id: sorted(
                [
                    prediction.home_probability,
                    prediction.draw_probability,
                    prediction.away_probability,
                ],
                reverse=True,
            )
            for prediction in predictions
        }
        decisions_by_match = {rec.match_id: rec.decisions for rec in recommendations}
        n = len(predictions)
        target_floor = math.ceil(n * self.DEFAULT_COVERAGE_TARGET_FRACTION)
        target_floor = max(1, min(target_floor, n))

        modes: list[TicketCoverageMode] = []
        for mode_key in ("simple", "doubles", "full"):
            effective_probs: list[float] = []
            for prediction in predictions:
                sorted_probs = prob_by_match[prediction.match_id]
                decision = decisions_by_match.get(prediction.match_id, {}).get(mode_key)
                if decision is None or decision.pick_type == "fixed":
                    effective_probs.append(sorted_probs[0])
                elif decision.pick_type == "double":
                    effective_probs.append(min(sorted_probs[0] + sorted_probs[1], 1.0))
                else:
                    effective_probs.append(1.0)

            expected_correct = sum(effective_probs)
            # Report P(>=K) for every K from 50% of slate up to perfect,
            # plus the explicit target. This is what the UI shows.
            checkpoints: dict[str, float] = {}
            for k in range(max(1, n // 2), n + 1):
                checkpoints[str(k)] = round(prob_at_least(effective_probs, k), 6)
            target_probability = checkpoints.get(str(target_floor), 0.0)
            # Jackpot = P(N/N), the only tier Progol Media Semana pays.
            # Near-jackpot = P(>= N-1/N), relevant for weekend payouts.
            jackpot_probability = prob_at_least(effective_probs, n)
            near_jackpot_probability = (
                prob_at_least(effective_probs, n - 1) if n >= 2 else jackpot_probability
            )
            tickets_for_half_chance = self._tickets_for_half_chance(jackpot_probability)
            modes.append(
                TicketCoverageMode(
                    mode=mode_key,
                    expected_correct=round(expected_correct, 4),
                    probabilities_at_least=checkpoints,
                    target_floor=target_floor,
                    target_probability=target_probability,
                    target_met=target_probability >= self.DEFAULT_COVERAGE_TARGET_PROBABILITY,
                    jackpot_probability=round(jackpot_probability, 6),
                    near_jackpot_probability=round(near_jackpot_probability, 6),
                    tickets_for_half_chance=tickets_for_half_chance,
                )
            )
        return modes

    @staticmethod
    def _tickets_for_half_chance(per_ticket_probability: float) -> int | None:
        """Return the minimum number of independent boletas such that
        the cumulative probability of at least one jackpot exceeds 50%.

        Returns None when per_ticket_probability is <=0 (jackpot is
        impossible, so no number of boletas helps) or >=1 (trivially
        already certain on one ticket — no calculation needed).
        """
        if per_ticket_probability <= 0.0 or per_ticket_probability >= 1.0:
            return None
        # cumulative P >= 0.5  <=>  1 - (1-p)^k >= 0.5  <=>  k >= log(0.5)/log(1-p)
        return math.ceil(math.log(0.5) / math.log(1.0 - per_ticket_probability))

    def _rule_for_slate(self, week_type: str, match_count: int) -> dict[str, int | str]:
        if week_type in self.MULTIPLE_RULES:
            selected = self.MULTIPLE_RULES[week_type]
        elif match_count >= 14:
            selected = self.MULTIPLE_RULES["weekend"]
        elif match_count <= 7:
            selected = self.MULTIPLE_RULES["revancha"]
        else:
            selected = self.MULTIPLE_RULES["fallback"]
        return {"week_type": week_type or "fallback", **selected}

    def _build_match_recommendation(
        self,
        *,
        prediction: MatchPredictionResponse,
        profile: dict[str, Any],
        double_ids: set[str],
        full_double_ids: set[str],
        full_triple_ids: set[str],
    ) -> MatchTicketRecommendationResponse:
        outcomes = self._sorted_outcomes(prediction)
        best, second, _third = outcomes
        decisions = {
            "simple": TicketDecisionResponse(pick_type="fixed", picks=[best[0]]),
            "doubles": self._doubles_decision(prediction, best, second, double_ids),
            "full": self._full_decision(prediction, best, second, full_double_ids, full_triple_ids),
        }
        return MatchTicketRecommendationResponse(
            position=prediction.position,
            match_id=prediction.match_id,
            decisions=decisions,
            validation=self._validation(profile),
        )

    def _doubles_decision(
        self,
        prediction: MatchPredictionResponse,
        best: tuple[Outcome, float],
        second: tuple[Outcome, float],
        double_ids: set[str],
    ) -> TicketDecisionResponse:
        best_gap = best[1] - second[1]
        if best[1] >= 0.58 and best_gap >= 0.12 and prediction.confidence_band != "low":
            return TicketDecisionResponse(pick_type="fixed", picks=[best[0]])
        if prediction.match_id in double_ids:
            return TicketDecisionResponse(pick_type="double", picks=[best[0], second[0]])
        return TicketDecisionResponse(pick_type="fixed", picks=[best[0]])

    def _full_decision(
        self,
        prediction: MatchPredictionResponse,
        best: tuple[Outcome, float],
        second: tuple[Outcome, float],
        full_double_ids: set[str],
        full_triple_ids: set[str],
    ) -> TicketDecisionResponse:
        if prediction.match_id in full_triple_ids:
            return TicketDecisionResponse(pick_type="triple", picks=list(self.OUTCOME_ORDER))
        if prediction.match_id in full_double_ids:
            return TicketDecisionResponse(pick_type="double", picks=[best[0], second[0]])
        return TicketDecisionResponse(pick_type="fixed", picks=[best[0]])

    def _choose_doubles(
        self,
        predictions: list[MatchPredictionResponse],
        profiles: dict[str, dict[str, Any]],
        limit: int | str,
    ) -> set[str]:
        """Doubles-only ticket: optimize the assignment of up to `limit`
        doubles to maximize the joint log-probability of going clean."""
        plan = optimize_ticket(
            self._options_from_predictions(predictions),
            max_doubles=int(limit),
            max_triples=0,
        )
        return {match_id for match_id, kind in plan.decisions.items() if kind == "double"}

    def _choose_full_coverage(
        self,
        predictions: list[MatchPredictionResponse],
        profiles: dict[str, dict[str, Any]],
        rule: dict[str, int | str],
    ) -> tuple[set[str], set[str]]:
        """Combined doubles+triples ticket: same optimizer with both budgets
        enabled. The DP returns the assignment that maximizes EV; we split
        it into the two id sets the caller expects."""
        plan = optimize_ticket(
            self._options_from_predictions(predictions),
            max_doubles=int(rule["combined_double_max"]),
            max_triples=int(rule["combined_triple_max"]),
        )
        double_ids = {match_id for match_id, kind in plan.decisions.items() if kind == "double"}
        triple_ids = {match_id for match_id, kind in plan.decisions.items() if kind == "triple"}
        return double_ids, triple_ids

    def _options_from_predictions(
        self, predictions: list[MatchPredictionResponse]
    ) -> list[TicketOption]:
        """Project predictions into the optimizer's input shape, sorting
        probabilities descending so top1/top2/top3 are well-defined."""
        options: list[TicketOption] = []
        for prediction in predictions:
            sorted_probs = sorted(
                [prediction.home_probability, prediction.draw_probability, prediction.away_probability],
                reverse=True,
            )
            options.append(
                TicketOption(
                    match_id=prediction.match_id,
                    top1=float(sorted_probs[0]),
                    top2=float(sorted_probs[1]),
                    top3=float(sorted_probs[2]),
                )
            )
        return options

    def _risk_profile(
        self,
        prediction: MatchPredictionResponse,
        feature_payload: dict[str, Any],
    ) -> dict[str, Any]:
        outcomes = self._sorted_outcomes(prediction)
        best, second, third = outcomes
        entropy = sum(
            -max(probability, 0.001) * math.log(max(probability, 0.001))
            for _outcome, probability in outcomes
        ) / math.log(3)
        top_gap = best[1] - second[1]
        second_gap = second[1] - third[1]
        confidence = prediction.confidence_band or "low"
        readiness = prediction.competition_readiness or "unclassified"
        evidence_count = int(float(feature_payload.get("evidence_items", 0) or 0))
        # Recent-form / H2H counts are the next-best anchors when no
        # scraped news evidence is linked. Use whichever is strongest so
        # partidos with solid match history (Liga MX, Brasileirao etc.)
        # don't get downgraded just because we don't run a per-match
        # news scraper.
        head_to_head_count = int(float(feature_payload.get("head_to_head_results_count", 0) or 0))
        recent_results_count = int(float(feature_payload.get("recent_results_count", 0) or 0))
        anchored_data_count = max(evidence_count, head_to_head_count, recent_results_count)
        confidence_risk = {
            "high": -0.08,
            "medium": 0.02,
            "low": 0.14,
            "blocked": 0.16,
        }.get(confidence, 0.08)
        readiness_risk = {
            "ready": -0.06,
            "covered": 0.02,
            "context_only": 0.08,
            "not_ready": 0.12,
            "unclassified": 0.12,
        }.get(readiness, 0.08)
        evidence_risk = (
            0.08 if anchored_data_count <= 0
            else 0.03 if anchored_data_count < 2
            else -0.02
        )
        gap_risk = 0.14 if top_gap <= 0.08 else 0.08 if top_gap <= 0.14 else 0.03 if top_gap <= 0.22 else -0.04
        third_outcome_risk = 0.09 if third[1] >= 0.24 else 0.05 if third[1] >= 0.20 else 0.0
        validation_risk = entropy + confidence_risk + readiness_risk + evidence_risk + gap_risk + third_outcome_risk
        return {
            "entropy": round(entropy, 4),
            "top_gap": round(top_gap, 4),
            "second_gap": round(second_gap, 4),
            "best_outcome": best[0],
            "second_outcome": second[0],
            "third_outcome": third[0],
            "best_probability": best[1],
            "second_probability": second[1],
            "third_probability": third[1],
            "confidence": confidence,
            "competition_readiness": readiness,
            "evidence_count": evidence_count,
            "head_to_head_count": head_to_head_count,
            "recent_results_count": recent_results_count,
            "anchored_data_count": anchored_data_count,
            "validation_risk": round(validation_risk, 4),
            "double_score": round(validation_risk + second[1] * 0.7 - top_gap * 0.35, 4),
            "triple_score": round(validation_risk + third[1] * 1.45 - top_gap * 0.18 - second_gap * 0.1, 4),
        }

    def _validation(self, profile: dict[str, Any]) -> TicketValidationResponse:
        top_gap = float(profile["top_gap"])
        confidence = str(profile["confidence"])
        readiness = str(profile["competition_readiness"])
        evidence_count = int(profile["evidence_count"])
        anchored_data_count = int(profile.get("anchored_data_count", evidence_count))
        third_probability = float(profile["third_probability"])
        validation_risk = float(profile["validation_risk"])
        reasons: list[str] = []
        if top_gap <= 0.08:
            reasons.append("brecha muy cerrada entre las dos primeras opciones")
        elif top_gap <= 0.18:
            reasons.append("brecha moderada entre las dos primeras opciones")
        else:
            reasons.append("brecha principal defendible")
        reasons.append(f"confianza {confidence}")
        if readiness in {"ready", "covered"}:
            reasons.append(f"benchmark {readiness}")
        else:
            reasons.append(f"benchmark {readiness}; no se trata como fijo seguro")
        reasons.append(f"{evidence_count} evidencia(s) ligada(s)")
        if third_probability >= 0.22:
            reasons.append("el tercer resultado conserva peso")

        level = "low"
        label = "Fijo defendible"
        recommendation = "Puede quedar simple si no hay presupuesto para cobertura."
        if top_gap <= 0.08 or validation_risk >= 1.16 or confidence == "low" or third_probability >= 0.24:
            level = "high"
            label = "No dejar simple"
            recommendation = "Priorizar doble o triple en completa."
        elif (
            top_gap <= 0.18
            or validation_risk >= 1.0
            or confidence == "blocked"
            or readiness not in {"ready", "covered"}
            or anchored_data_count <= 1
        ):
            level = "medium"
            label = "Cubrir si hay presupuesto"
            recommendation = "Mantener simple solo si la papeleta ya agoto dobles/triples."
        return TicketValidationResponse(
            level=level,
            label=label,
            recommendation=recommendation,
            reasons=reasons,
            metrics={key: value for key, value in profile.items() if key not in {"double_score", "triple_score"}},
        )

    def _sorted_outcomes(self, prediction: MatchPredictionResponse) -> list[tuple[Outcome, float]]:
        return sorted(
            [
                (Outcome.HOME, prediction.home_probability),
                (Outcome.DRAW, prediction.draw_probability),
                (Outcome.AWAY, prediction.away_probability),
            ],
            key=lambda item: item[1],
            reverse=True,
        )
