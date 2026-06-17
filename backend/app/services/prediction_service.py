import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.domain.entities import Outcome, Prediction
from app.models.tables import MatchModel
from app.models.tables import ProgolSlateModel
from app.repositories.feature_repository import FeatureRepository
from app.repositories.evidence_dedupe import dedupe_evidence_items
from app.repositories.result_repository import ResultRepository
from app.services.feature_service import FeatureService
from app.services.model_training_service import ModelTrainingService
from app.services.sanity_service import (
    SANITY_POLICY_VERSION,
    EvidenceLevel,
    apply_sanity_layer,
)
from app.schemas.prediction import MatchPredictionResponse


logger = logging.getLogger(__name__)


# Module-level TTL cache for slate predictions. Each of the three slate
# endpoints (/predictions/slates/{id}, /ticket, /quality) recomputes the
# same 14-match scoring pipeline on every call. The frontend hits all
# three in parallel on every page load, so a short cache cuts the
# wall-clock load from ~2.5s to ~0.8s without changing inputs/outputs.
_SLATE_PREDICTION_TTL_SECONDS = 30.0
_slate_prediction_cache: dict[str, tuple[float, list[MatchPredictionResponse]]] = {}


def _cached_slate_predictions(slate_id: str) -> list[MatchPredictionResponse] | None:
    entry = _slate_prediction_cache.get(slate_id)
    if entry is None:
        return None
    cached_at, value = entry
    if time.monotonic() - cached_at > _SLATE_PREDICTION_TTL_SECONDS:
        _slate_prediction_cache.pop(slate_id, None)
        return None
    return value


def _store_slate_predictions(slate_id: str, predictions: list[MatchPredictionResponse]) -> None:
    _slate_prediction_cache[slate_id] = (time.monotonic(), predictions)


def invalidate_slate_prediction_cache(slate_id: str | None = None) -> None:
    if slate_id is None:
        _slate_prediction_cache.clear()
    else:
        _slate_prediction_cache.pop(slate_id, None)


class PredictionService:
    """Build auditable match predictions from the latest trained artifact or heuristic fallback."""

    def __init__(self, training_service: ModelTrainingService | None = None) -> None:
        self.training_service = training_service
        self.feature_service = (
            FeatureService(
                FeatureRepository(training_service.training_repository.session),
                ResultRepository(training_service.training_repository.session),
            )
            if training_service is not None
            else None
        )

    def build_placeholder_prediction(self, match_id: str) -> Prediction:
        return Prediction(
            id=f"pred-{match_id}",
            match_id=match_id,
            generated_at=datetime.now(timezone.utc),
            home_probability=0.4,
            draw_probability=0.3,
            away_probability=0.3,
            recommended_outcome=Outcome.HOME,
            confidence_band="low",
        )

    def build_slate_predictions(self, slate: ProgolSlateModel) -> list[MatchPredictionResponse]:
        cached = _cached_slate_predictions(slate.id)
        if cached is not None:
            return cached
        responses: list[MatchPredictionResponse] = []

        # Stable id of the artifact scoring this slate (same for every
        # match), stamped into each audit row for traceability.
        model_artifact_id: str | None = None
        if self.training_service is not None:
            artifact_id_fn = getattr(self.training_service, "current_model_artifact_id", None)
            if artifact_id_fn is not None:
                try:
                    candidate = artifact_id_fn()
                except Exception:  # pragma: no cover - never block scoring on audit metadata
                    candidate = None
                # Coerce to a plain str so the audit JSON stays serializable
                # even when a test stubs the training service with a Mock.
                model_artifact_id = candidate if isinstance(candidate, str) else None

        for slate_match in sorted(slate.matches, key=lambda item: item.position):
            match: MatchModel = slate_match.match
            competition_policy = self._competition_policy_for_match(match)
            if self.training_service is None:
                prediction = self.build_placeholder_prediction(match.id)
                scored = {
                    "home": prediction.home_probability,
                    "draw": prediction.draw_probability,
                    "away": prediction.away_probability,
                }
            else:
                scored = self.training_service.score_match(match)
                prediction = Prediction(
                    id=f"pred-{match.id}",
                    match_id=match.id,
                    generated_at=datetime.now(timezone.utc),
                    home_probability=scored["home"],
                    draw_probability=scored["draw"],
                    away_probability=scored["away"],
                    recommended_outcome=Outcome.HOME,
                    confidence_band="low",
                )
            feature_map = self._feature_map_for_match(match)
            evidence_count = int(feature_map.get("evidence_count", len(getattr(match, "evidence_items", []))))
            adjusted_home, adjusted_draw, adjusted_away = self._adjust_probabilities(scored, feature_map)
            is_knockout = bool(getattr(slate_match, "is_knockout", False))
            knockout_note: str | None = None
            if is_knockout:
                (
                    adjusted_home,
                    adjusted_draw,
                    adjusted_away,
                    knockout_note,
                ) = self._apply_knockout_adjustment(
                    adjusted_home,
                    adjusted_draw,
                    adjusted_away,
                    feature_map,
                    competition_name=match.competition.name,
                )
            # Band on the FINAL probability vector — for knockouts that
            # means the post-E=0 redistribution where the comparison is
            # binary (L vs V). The knockout-aware thresholds in
            # _confidence_band correct for that shape.
            sorted_probabilities = sorted([adjusted_home, adjusted_draw, adjusted_away], reverse=True)
            confidence_band = self._confidence_band(
                sorted_probabilities[0],
                sorted_probabilities[1],
                evidence_count,
                feature_map,
                is_knockout=is_knockout,
            )
            if competition_policy["competition_readiness"] == "unclassified":
                confidence_band = "blocked"
            # Fase 5.5: hard gate on data sufficiency. If the feature vector
            # has no recent form for either team and no head-to-head, the
            # model is extrapolating from training-set bias against a vector
            # of zeros — the prediction is not anchored in evidence. We mark
            # the match `blocked` so the UI surfaces this explicitly instead
            # of presenting hollow probabilities as if they were grounded.
            if self._has_insufficient_data(feature_map):
                confidence_band = "blocked"
            outcome_candidates: list[tuple[float, Outcome]] = [
                (adjusted_home, Outcome.HOME),
                (adjusted_away, Outcome.AWAY),
            ]
            if not is_knockout:
                outcome_candidates.append((adjusted_draw, Outcome.DRAW))
            # A knockout / final must produce a winner — the boleta in
            # those positions doesn't accept X. The model probabilities
            # were already shrunk above so the L/V mass reflects the
            # data-anchored attacking rhythm; we still belt-and-
            # suspenders the choice here to guarantee the pick is L or
            # V even if the shrinkage left X technically highest.
            recommended_outcome = max(
                outcome_candidates,
                key=lambda item: item[0],
            )[1]
            rationale = self._build_rationale(
                match,
                evidence_count,
                adjusted_home,
                adjusted_draw,
                adjusted_away,
                feature_map,
                competition_policy,
            )
            if knockout_note is not None:
                rationale.insert(0, knockout_note)

            # --- Fase 3/4: sanity guardrail layer --------------------------
            # Runs over the model's post-processed vector and produces the
            # auditable, guardrailed display fields. It never overwrites the
            # raw numbers silently — both raw and final are surfaced.
            evidence_level = self._evidence_level(feature_map, evidence_count)
            is_friendly = self._is_international_friendly(competition_policy)
            fallback_used = self._fallback_used(match)
            sanity = apply_sanity_layer(
                probabilities={
                    "home": adjusted_home,
                    "draw": adjusted_draw,
                    "away": adjusted_away,
                },
                confidence_band=confidence_band,
                evidence_level=evidence_level,
                is_international_friendly=is_friendly,
                fallback_used=fallback_used,
                is_knockout=is_knockout,
                recommended_outcome=recommended_outcome.value,
            )
            if sanity.flags:
                rationale.append(
                    "Capa de seguridad: "
                    + ", ".join(sanity.flag_values())
                    + f" -> estado {sanity.final_status.value} (riesgo {sanity.risk_level.value})."
                )

            # The single guardrailed (sanity-degraded) vector. It is the
            # source of truth for BOTH what the UI displays and what the
            # ticket optimizer decides on. The legacy positional fields
            # below are set to these same safe values so no downstream
            # consumer can be misled by raw model numbers — `raw_probabilities`
            # keeps the original model output for traceability.
            decision_home = sanity.final_probabilities["home"]
            decision_draw = sanity.final_probabilities["draw"]
            decision_away = sanity.final_probabilities["away"]
            decision_vector = {"L": decision_home, "E": decision_draw, "V": decision_away}
            raw_vector = {
                "L": sanity.raw_probabilities["home"],
                "E": sanity.raw_probabilities["draw"],
                "V": sanity.raw_probabilities["away"],
            }
            responses.append(
                MatchPredictionResponse(
                    slate_id=slate.id,
                    position=slate_match.position,
                    match_id=match.id,
                    competition_name=match.competition.name,
                    home_team_name=match.home_team.name,
                    away_team_name=match.away_team.name,
                    generated_at=prediction.generated_at,
                    # Legacy positional fields = guardrailed decision values
                    # (not raw). This eliminates the contradiction where the
                    # optimizer could consume raw probabilities via these.
                    home_probability=decision_home,
                    draw_probability=decision_draw,
                    away_probability=decision_away,
                    recommended_outcome=recommended_outcome,
                    competition_readiness=str(competition_policy["competition_readiness"]),
                    live_pick_allowed=bool(competition_policy["live_pick_allowed"]),
                    policy_reason=str(competition_policy["policy_reason"]),
                    confidence_band=confidence_band,
                    rationale=rationale,
                    is_knockout=is_knockout,
                    probabilities=dict(decision_vector),
                    display_probabilities=dict(decision_vector),
                    decision_probabilities=dict(decision_vector),
                    labels={"L": "Local", "E": "Empate", "V": "Visitante"},
                    raw_probabilities=raw_vector,
                    evidence_level=evidence_level.value,
                    confidence=sanity.confidence,
                    visible_confidence=sanity.visible_confidence,
                    confidence_explanation=list(sanity.confidence_explanation),
                    risk_level=sanity.risk_level.value,
                    final_status=sanity.final_status.value,
                    ticket_strategy=sanity.ticket_strategy,
                    ticket_strategy_label=sanity.ticket_strategy_label,
                    ticket_strategy_reason=sanity.ticket_strategy_reason,
                    flags=sanity.flag_values(),
                    fallback_used=fallback_used,
                    is_international_friendly=is_friendly,
                    sanity_recommendation=sanity.recommendation,
                )
            )
            # Full guardrail trace for the DB audit. `optimizer_probabilities`
            # is the vector the ticket optimizer actually consumes — which is
            # `decision_probabilities` (it reads `decision_vector()`), so they
            # are equal by construction; we persist both explicitly so a
            # future divergence is detectable from the audit alone.
            sanity_audit = {
                "raw_probabilities": dict(raw_vector),
                "display_probabilities": dict(decision_vector),
                "decision_probabilities": dict(decision_vector),
                "optimizer_probabilities": dict(decision_vector),
                "sanity_flags": sanity.flag_values(),
                "risk_level": sanity.risk_level.value,
                "evidence_level": evidence_level.value,
                "visible_confidence": sanity.visible_confidence,
                "ticket_strategy": sanity.ticket_strategy,
                "final_status": sanity.final_status.value,
                "sanity_policy_version": SANITY_POLICY_VERSION,
                "model_artifact_id": model_artifact_id,
                "fallback_used": fallback_used,
                "is_international_friendly": is_friendly,
            }
            self._persist_prediction_audit(
                match_id=match.id,
                slate_id=slate.id,
                composition_hash=getattr(slate, "composition_hash", None),
                slate_version=getattr(slate, "slate_version", None),
                generated_at=prediction.generated_at,
                # MODEL-adjusted values: the backtesting source of truth.
                # NOT overwritten by the sanity decision (kept in the trace).
                home_probability=adjusted_home,
                draw_probability=adjusted_draw,
                away_probability=adjusted_away,
                recommended_outcome=recommended_outcome.value,
                confidence_band=confidence_band,
                competition_readiness=str(competition_policy["competition_readiness"]),
                feature_map=feature_map,
                sanity_audit=sanity_audit,
            )

        _store_slate_predictions(slate.id, responses)
        return responses

    # Fallback bounds used when the training service is missing (unit
    # tests with mocked dependencies) or the competition has no
    # calibrated draw rate yet. Tighter than the pre-calibration
    # constants because the unlabeled case shouldn't overcorrect.
    KNOCKOUT_SHRINK_MIN_FALLBACK = 0.15
    KNOCKOUT_SHRINK_MAX_FALLBACK = 0.55

    def _apply_knockout_adjustment(
        self,
        home_p: float,
        draw_p: float,
        away_p: float,
        feature_map: dict[str, float],
        *,
        competition_name: str | None = None,
    ) -> tuple[float, float, float, str]:
        """Redistribute the draw probability for a knockout fixture.

        Knockout matches resolve to a winner (ET / penalties), so the
        90-minute draw mass that the model produced reflects league
        habits more than final-match dynamics. We shrink the draw
        proportionally to the expected goal output of the pair —
        higher rhythm = more likely a winner emerges in regulation —
        and redistribute the freed mass to L and V weighted by their
        existing probabilities.

        The shrinkage is data-anchored on two axes:
        * Team-level: each side's goals_for_per_match and
          goals_against_per_match from recent results decide where in
          the band we land for THIS match.
        * League-level: the competition's empirical draw rate
          (vs the empirical knockout target ~22%) decides the band
          itself. Liga MX (high-draw) ends up with a wider shrinkage
          range than the Premier League (lower-draw, less to remove).

        With no recent team data we fall back to the floor of the
        calibrated band so we still respect the boleta rule without
        pretending to know more than we do. With no league
        calibration data we use the fallback band — both bounds
        tighter than the pre-calibration global default.

        Returns the new (home, draw, away, rationale_note) tuple.
        """
        if competition_name and self.training_service is not None:
            shrink_min, shrink_max, diagnostics = (
                self.training_service.knockout_shrinkage_bounds(competition_name)
            )
        else:
            shrink_min, shrink_max = (
                self.KNOCKOUT_SHRINK_MIN_FALLBACK,
                self.KNOCKOUT_SHRINK_MAX_FALLBACK,
            )
            diagnostics = {"league_draw_rate": 0.0, "baseline": 0.0, "calibrated": 0.0}

        home_gf = float(feature_map.get("home_goals_for_per_match", 0.0))
        away_gf = float(feature_map.get("away_goals_for_per_match", 0.0))
        home_ga = float(feature_map.get("home_goals_against_per_match", 0.0))
        away_ga = float(feature_map.get("away_goals_against_per_match", 0.0))
        # Expected goals proxy: each side's attack adjusted by the
        # opponent's leakage. We average the two perspectives so
        # neither side dominates.
        expected_total = ((home_gf + away_ga) + (away_gf + home_ga)) / 2.0

        home_recent = float(feature_map.get("home_recent_matches", 0.0))
        away_recent = float(feature_map.get("away_recent_matches", 0.0))
        thin_data = home_recent < 1 and away_recent < 1

        league_label = ""
        if diagnostics.get("calibrated", 0.0) >= 0.5:
            league_label = (
                f"liga ~{diagnostics['league_draw_rate'] * 100:.0f}% E historico"
            )

        if thin_data:
            shrinkage = shrink_min
            anchor_note = "datos historicos limitados"
        else:
            # Interpolate between shrink_min and shrink_max using the
            # expected-goals signal. Baseline of 1.0 goals/match maps
            # to the band floor; 4.0 goals/match maps to the cap.
            # Clamped to [0, 1] so the band edges hold.
            interpolation = max(0.0, min(1.0, (expected_total - 1.0) / 3.0))
            # interpolation: 0 when expected=1.0 or below, 1 when expected=4.0
            shrinkage = shrink_min + (shrink_max - shrink_min) * interpolation
            anchor_note = f"ritmo esperado {expected_total:.1f} goles/partido"
            if league_label:
                anchor_note = f"{anchor_note} ({league_label})"

        # Boleta rule for knockouts: E is not a valid outcome, so the
        # displayed probability must be 0%. We redistribute 100% of the
        # draw mass to L/V (proportional to their existing weights) and
        # keep the per-league `shrinkage` only as a diagnostic of how
        # aggressive the redistribution would have been on a soft model.
        freed_mass = draw_p
        new_draw = 0.0
        total_ha = home_p + away_p
        if total_ha > 1e-9:
            new_home = home_p + freed_mass * (home_p / total_ha)
            new_away = away_p + freed_mass * (away_p / total_ha)
        else:
            new_home = home_p + freed_mass * 0.5
            new_away = away_p + freed_mass * 0.5

        total = new_home + new_draw + new_away
        if total > 1e-9:
            new_home /= total
            new_draw /= total
            new_away /= total

        shrinkage_pct = int(round(shrinkage * 100))
        note = (
            f"Eliminatoria: empate descartado por la boleta (E=0%). "
            f"Toda la masa de E redistribuida a L/V proporcional "
            f"(banda calibrada {shrinkage_pct}%, {anchor_note}); "
            f"probabilidades ajustadas L={new_home:.2f} E={new_draw:.2f} V={new_away:.2f}."
        )
        return new_home, new_draw, new_away, note

    def _persist_prediction_audit(
        self,
        *,
        match_id: str,
        slate_id: str | None = None,
        composition_hash: str | None = None,
        slate_version: int | None = None,
        generated_at: datetime,
        home_probability: float,
        draw_probability: float,
        away_probability: float,
        recommended_outcome: str,
        confidence_band: str,
        competition_readiness: str,
        feature_map: dict[str, float],
        sanity_audit: dict[str, Any] | None = None,
    ) -> None:
        """Persist a row to the predictions table so blocked / low-band
        decisions have a durable audit trail beyond log rotation.

        ``home/draw/away_probability`` are the MODEL-adjusted values (the
        backtesting source). ``sanity_audit`` carries the decision-time
        guardrail trace (raw/display/decision/optimizer vectors, flags,
        evidence/risk/status, policy version, artifact id, fallback) and is
        serialized to ``sanity_audit_json`` WITHOUT touching the model
        probability columns.

        The row is appended to the request-scoped session; the FastAPI
        managed_transaction context handles commit on response success
        (or rollback on error). Failures here are caught and logged so a
        broken audit insert never blocks the user-facing prediction
        response — the audit is bookkeeping, the response is the
        product.
        """
        if self.training_service is None:
            return
        session = self.training_service.training_repository.session
        # Some unit tests stub the training_repository with a bare
        # `object()` for `.session`. Skip persistence in that case
        # instead of crashing — the audit is non-essential here.
        if not hasattr(session, "add") or not hasattr(session, "flush"):
            return
        try:
            from app.models.tables import PredictionModel

            blocked_reason: str | None = None
            if confidence_band == "blocked":
                if competition_readiness == "unclassified":
                    blocked_reason = "unclassified_competition"
                elif self._has_insufficient_data(feature_map):
                    blocked_reason = "insufficient_data_anchors"
                else:
                    blocked_reason = "blocked_other"

            anchors = {
                "home_recent_matches": float(feature_map.get("home_recent_matches", 0.0)),
                "away_recent_matches": float(feature_map.get("away_recent_matches", 0.0)),
                "head_to_head_matches": float(feature_map.get("head_to_head_matches", 0.0)),
                "evidence_count": float(feature_map.get("evidence_count", 0.0)),
            }

            session.add(
                PredictionModel(
                    match_id=match_id,
                    slate_id=slate_id,
                    composition_hash=composition_hash,
                    slate_version=slate_version,
                    generated_at=generated_at,
                    home_probability=float(home_probability),
                    draw_probability=float(draw_probability),
                    away_probability=float(away_probability),
                    recommended_outcome=recommended_outcome,
                    confidence_band=confidence_band,
                    competition_readiness=competition_readiness,
                    blocked_reason=blocked_reason,
                    anchors_json=json.dumps(anchors),
                    sanity_audit_json=(
                        json.dumps(sanity_audit) if sanity_audit is not None else None
                    ),
                )
            )
            session.flush()
            # The default FastAPI db dep (`get_db_session`) yields a
            # session that closes without committing — pending writes
            # would be rolled back when the request handler returns.
            # The audit row is a side-effect bookkeeping write and
            # has no dependency on whatever else the route is doing,
            # so commit it explicitly here.
            session.commit()
        except Exception:
            logger.exception(
                "prediction audit insert failed",
                extra={"event": "prediction_audit_insert_failed", "match_id": match_id},
            )
            session.rollback()

    def _feature_map_for_match(self, match: MatchModel) -> dict[str, float]:
        if self.feature_service is None:
            return {"evidence_count": float(len(getattr(match, "evidence_items", [])))}
        return self.feature_service.build_model_features(match, cutoff=match.kickoff_at)

    # When the union of recent matches (both sides) + H2H is below this
    # threshold the prediction is treated as data-insufficient. The number
    # is intentionally conservative: with fewer than 3 historical events
    # any feature-driven probability is just XGBoost's bias projected
    # onto an almost-empty vector.
    MIN_DATA_ANCHORS = 4
    # Both sides must bring at least this many recent matches, OR the
    # pair must have this many head-to-heads. One side at zero with a
    # single H2H used to slip through and produce bias-driven picks —
    # the audit caught it on Tampico vs Tepatitlán. Tightened in
    # Fase 2.7.
    MIN_RECENT_PER_SIDE = 2
    MIN_H2H = 3

    def _has_insufficient_data(self, feature_map: dict[str, float]) -> bool:
        """Return True when the feature vector cannot support a grounded
        prediction.

        Two checks must pass for the match to *not* be blocked:

        1. Union threshold: `home_recent + away_recent + h2h >= 4`.
           Fewer than 4 anchoring events means the model is mostly
           extrapolating from training-set bias.
        2. Two-sided coverage: either both teams contribute at least
           ``MIN_RECENT_PER_SIDE`` (2) recent matches, or the pair has
           at least ``MIN_H2H`` (3) head-to-head events. A single H2H
           or one team at zero recent matches no longer passes — a
           ghost side projected through XGBoost would otherwise return
           a confident probability against a bias vector.

        Both checks are intentionally strict so the UI never markets
        a bias-driven prediction as 'medium' or 'high' confidence."""
        home_recent = float(feature_map.get("home_recent_matches", 0.0))
        away_recent = float(feature_map.get("away_recent_matches", 0.0))
        head_to_head = float(feature_map.get("head_to_head_matches", 0.0))
        total_anchors = home_recent + away_recent + head_to_head
        if total_anchors < self.MIN_DATA_ANCHORS:
            return True
        both_sides_have_form = (
            home_recent >= self.MIN_RECENT_PER_SIDE
            and away_recent >= self.MIN_RECENT_PER_SIDE
        )
        h2h_present = head_to_head >= self.MIN_H2H
        return not (both_sides_have_form or h2h_present)

    def _adjust_probabilities(
        self,
        scored: dict[str, float],
        feature_map: dict[str, float],
    ) -> tuple[float, float, float]:
        home_probability = float(scored["home"])
        draw_probability = float(scored["draw"])
        away_probability = float(scored["away"])
        evidence_count = float(feature_map.get("evidence_count", 0.0))
        head_to_head_matches = float(feature_map.get("head_to_head_matches", 0.0))
        has_context = evidence_count > 0 or head_to_head_matches > 0
        if not has_context:
            return self._normalize_probabilities(home_probability, draw_probability, away_probability)

        home_signal_total = sum(
            float(feature_map.get(name, 0.0))
            for name in ("home_injury_signals", "home_suspension_signals", "home_rotation_signals")
        )
        away_signal_total = sum(
            float(feature_map.get(name, 0.0))
            for name in ("away_injury_signals", "away_suspension_signals", "away_rotation_signals")
        )
        head_to_head_edge = (
            float(feature_map.get("head_to_head_points_gap", 0.0)) * 0.25
            + float(feature_map.get("head_to_head_goal_balance_gap", 0.0)) * 0.18
        )
        directional_context_edge = (
            float(feature_map.get("away_context_signal", 0.0))
            - float(feature_map.get("home_context_signal", 0.0))
            + (away_signal_total - home_signal_total) * 0.15
            + head_to_head_edge
        )
        if abs(directional_context_edge) < 0.01:
            return self._normalize_probabilities(home_probability, draw_probability, away_probability)

        swing = min(abs(directional_context_edge) * 0.06, 0.1)
        draw_reduction = min(max(draw_probability - 0.12, 0.0), swing * 0.35)
        if directional_context_edge > 0:
            home_probability += swing + (draw_reduction / 2)
            away_probability = max(away_probability - swing, 0.05)
        else:
            away_probability += swing + (draw_reduction / 2)
            home_probability = max(home_probability - swing, 0.05)
        draw_probability -= draw_reduction
        return self._normalize_probabilities(home_probability, draw_probability, away_probability)

    def _normalize_probabilities(
        self,
        home_probability: float,
        draw_probability: float,
        away_probability: float,
    ) -> tuple[float, float, float]:
        total = home_probability + draw_probability + away_probability
        if total <= 0:
            return 0.4, 0.3, 0.3
        return (
            round(home_probability / total, 2),
            round(draw_probability / total, 2),
            round(away_probability / total, 2),
        )

    def _confidence_band(
        self,
        top_probability: float,
        second_probability: float,
        evidence_count: int,
        feature_map: dict[str, float] | None = None,
        *,
        is_knockout: bool = False,
    ) -> str:
        spread = top_probability - second_probability
        # No per-match news scraper runs yet, so evidence_count is 0 on
        # nearly every fixture. Accept H2H or two-sided recent form as an
        # equivalent anchor — that is the same shape of "we have real
        # data on this match" that evidence_count was originally meant to
        # gate on.
        feature_map = feature_map or {}
        h2h = float(feature_map.get("head_to_head_matches", 0.0))
        home_recent = float(feature_map.get("home_recent_matches", 0.0))
        away_recent = float(feature_map.get("away_recent_matches", 0.0))
        anchored = (
            evidence_count >= 1
            or h2h >= 2
            or (home_recent >= 3 and away_recent >= 3)
        )
        # Knockouts: after the E=0 redistribution the comparison is
        # binary (L vs V). The 3-class spread thresholds undersell the
        # signal — drop the spread guard and band on top alone.
        # high threshold = 0.55 because the residual mass on the second
        # outcome is at most 0.45 by construction (top + second = 1
        # after E=0), so a 0.55 favourite carries a real edge — the
        # 0.60 we used before was carried over from the 3-class scale.
        if is_knockout:
            if top_probability >= 0.55 and anchored:
                return "high"
            if top_probability >= 0.50:
                return "medium"
            return "low"
        if top_probability >= 0.55 and spread >= 0.12 and anchored:
            return "high"
        if top_probability >= 0.40 and spread >= 0.02 and anchored:
            return "medium"
        return "low"

    def _build_rationale(
        self,
        match: MatchModel,
        evidence_count: int,
        home_probability: float,
        draw_probability: float,
        away_probability: float,
        feature_map: dict[str, Any],
        competition_policy: dict[str, object],
    ) -> list[str]:
        rationale = [
            f"Probabilidades L/E/V: {home_probability:.2f}/{draw_probability:.2f}/{away_probability:.2f}.",
            f"Evidencia enlazada al partido: {evidence_count}.",
            f"Politica de competencia: {competition_policy['competition_readiness']} ({competition_policy['policy_reason']}).",
        ]
        if self._has_insufficient_data(feature_map):
            rationale.insert(
                0,
                "ADVERTENCIA: prediccion sin anclaje en datos. "
                "Ningun equipo tiene forma reciente cargada ni historial directo. "
                "Las probabilidades reflejan el sesgo del modelo entrenado en otra liga, no este partido.",
            )
        rationale.extend(self._context_rationale_notes(match, evidence_count))
        if self.feature_service is None:
            return rationale

        form_gap = float(feature_map.get("form_gap", 0.0))
        goal_gap = float(feature_map.get("goal_balance_gap", 0.0))
        rest_gap = float(feature_map.get("rest_gap_days", 0.0))
        head_to_head_matches = int(float(feature_map.get("head_to_head_matches", 0.0)))
        head_to_head_points_gap = float(feature_map.get("head_to_head_points_gap", 0.0))
        head_to_head_goal_gap = float(feature_map.get("head_to_head_goal_balance_gap", 0.0))
        home_recent_matches = int(float(feature_map.get("home_recent_matches", 0.0)))
        away_recent_matches = int(float(feature_map.get("away_recent_matches", 0.0)))
        context_gap = float(feature_map.get("away_context_signal", 0.0)) - float(
            feature_map.get("home_context_signal", 0.0)
        )
        rationale.append(
            f"Muestra de forma reciente: local {home_recent_matches} partidos, visita {away_recent_matches} partidos."
        )
        h2h_count = int(float(feature_map.get("head_to_head_matches", 0.0)))
        _anchored = (
            evidence_count >= 1
            or h2h_count >= 2
            or (home_recent_matches >= 3 and away_recent_matches >= 3)
        )
        if not _anchored:
            anchor_gaps: list[str] = []
            if home_recent_matches < 3:
                anchor_gaps.append(
                    f"local tiene {home_recent_matches} resultado(s) reciente(s) (necesita 3)"
                )
            if away_recent_matches < 3:
                anchor_gaps.append(
                    f"visita tiene {away_recent_matches} resultado(s) reciente(s) (necesita 3)"
                )
            if h2h_count < 2:
                anchor_gaps.append(
                    f"historial directo insuficiente ({h2h_count} enfrentamiento(s), necesita 2)"
                )
            if anchor_gaps:
                rationale.append(
                    "Sin anclaje de confianza: "
                    + "; ".join(anchor_gaps)
                    + ". Calificatorias u otros partidos recientes pueden quedar fuera de la "
                    "ventana de forma activa — esto limita la banda a low."
                )
        rationale.append(
            f"Brecha de forma {form_gap:+.2f}, balance de goles {goal_gap:+.2f}, descanso {rest_gap:+.1f}d."
        )
        if head_to_head_matches > 0:
            rationale.append(
                "Historial directo: "
                f"{head_to_head_matches} partido(s), puntos local/visita {head_to_head_points_gap:+.2f} por juego, "
                f"goles {head_to_head_goal_gap:+.2f} por juego."
            )
        else:
            rationale.append("Sin historial directo enlazado entre estos dos equipos.")
        if abs(context_gap) >= 0.15:
            affected_side = "visitante" if context_gap > 0 else "local"
            rationale.append(f"La disponibilidad penaliza mas al lado {affected_side}.")
        else:
            rationale.append("No hay lesiones, suspensiones o reportes de alineacion confirmados para este partido.")
        return rationale

    def _context_rationale_notes(self, match: MatchModel, evidence_count: int) -> list[str]:
        if evidence_count <= 0:
            return ["No hay una fuente de contexto enlazada; este pronostico usa estimacion base."]

        notes: list[str] = []
        raw_evidence_items = dedupe_evidence_items(
            [
                item
                for item in getattr(match, "evidence_items", [])
                if hasattr(item, "captured_at") and hasattr(item, "payload_json")
            ]
        )
        evidence_items = sorted(
            raw_evidence_items,
            key=lambda item: item.captured_at,
            reverse=True,
        )
        for item in evidence_items[:2]:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(item.payload_json)
            except json.JSONDecodeError:
                payload = {}
            context = str(payload.get("context_summary") or item.summary).strip()
            if len(context) > 260:
                context = f"{context[:257].rstrip()}..."
            notes.append(f"Contexto verificado: {context}")
        return notes

    # Evidence-level thresholds. HIGH requires a genuinely deep sample on
    # both sides (or a rich head-to-head); anything that only barely clears
    # the anchoring gate is MEDIUM; an unanchored / data-insufficient
    # vector is LOW. These are deliberately stricter than the confidence
    # band so a national-team friendly with thin form never reads HIGH.
    EVIDENCE_HIGH_RECENT_PER_SIDE = 5
    EVIDENCE_HIGH_H2H = 5

    def _evidence_level(self, feature_map: dict[str, float], evidence_count: int) -> EvidenceLevel:
        if self._has_insufficient_data(feature_map):
            return EvidenceLevel.LOW
        h2h = float(feature_map.get("head_to_head_matches", 0.0))
        home_recent = float(feature_map.get("home_recent_matches", 0.0))
        away_recent = float(feature_map.get("away_recent_matches", 0.0))
        anchored = (
            evidence_count >= 1
            or h2h >= 2
            or (home_recent >= 3 and away_recent >= 3)
        )
        if not anchored:
            return EvidenceLevel.LOW
        deep_form = (
            home_recent >= self.EVIDENCE_HIGH_RECENT_PER_SIDE
            and away_recent >= self.EVIDENCE_HIGH_RECENT_PER_SIDE
        )
        deep_h2h = h2h >= self.EVIDENCE_HIGH_H2H
        if (deep_form or deep_h2h) and evidence_count >= 1:
            return EvidenceLevel.HIGH
        if deep_form or deep_h2h:
            return EvidenceLevel.MEDIUM
        return EvidenceLevel.MEDIUM

    @staticmethod
    def _is_international_friendly(competition_policy: dict[str, object]) -> bool:
        return str(competition_policy.get("competition_key", "")) == "international-friendlies"

    def _fallback_used(self, match: MatchModel) -> bool:
        """True when the prediction came from a non-ML heuristic fallback
        rather than the trained XGBoost booster."""
        if self.training_service is None:
            return True
        engine_fn = getattr(self.training_service, "prediction_engine_for_match", None)
        if engine_fn is None:
            return False
        try:
            return engine_fn(match) != "xgboost"
        except Exception:  # pragma: no cover - defensive; never block a prediction
            logger.exception("fallback engine lookup failed", extra={"match_id": match.id})
            return False

    def _competition_policy_for_match(self, match: MatchModel) -> dict[str, object]:
        if self.training_service is None or not hasattr(self.training_service, "competition_operating_policy"):
            return {
                "competition_readiness": "unclassified",
                "live_pick_allowed": False,
                "policy_reason": "No benchmark policy is available to validate this competition.",
            }
        return self.training_service.competition_operating_policy(match.competition.name)
