"""Controlled-activation dry-run for the team-rating gate (R5.5).

Answers "if we enabled the controlled gate, what would change?" — per match:
which engine, simulated probabilities, simulated pick, deltas vs the current
persisted prediction, and what blocks real activation. It is strictly
diagnostic and read-only:

* never writes a row (``SET TRANSACTION READ ONLY`` + no add/flush/commit),
* never calls ``FeatureService.build_match_features`` / ``PredictionService`` /
  ``TicketRecommendationService``,
* never changes real probabilities, picks, tickets or the approval gate.

All gate / routing / calibrator rules are reused verbatim from the already
audited ``scripts.audit_team_rating_shadow`` module — no rule is re-derived.

The simulated ("dry-run") probabilities come from the R5.3 calibrator candidate
(``international_friendlies_temperature_v1``), i.e. probability-space temperature
scaling of the *current* model probabilities. Temperature scaling is monotonic,
so it recalibrates confidence without reordering outcomes — the dry-run can
change probabilities and confidence buckets but typically not the top pick.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.domain.team_rating_calibrator import get_team_rating_calibrator_candidate
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.schemas.team_rating_activation_dry_run import (
    TeamRatingActivationDryRunResponse,
)
from scripts.audit_team_rating_shadow import audit_shadow
from scripts.audit_team_rating_shadow import _enforce_read_only_transaction
from scripts.audit_team_rating_shadow import _latest_predictions_by_match

# Controlled activation policy simulated by R5.5. REVISAR keeps blocking;
# review_allowed_shadow is intentionally NOT used for activation dry-run.
DRY_RUN_CALIBRATOR_CANDIDATE_ID = "international_friendlies_temperature_v1"
DRY_RUN_ROUTING_POLICY = "rating_replaces_fallback"
DRY_RUN_PROBABILITY_MODEL = DRY_RUN_CALIBRATOR_CANDIDATE_ID

_OUTCOME_KEYS = ("1", "X", "2")
_VECTOR_TO_OUTCOME = {"home": "1", "draw": "X", "away": "2"}


def _top_pick(probs: dict[str, float] | None) -> str | None:
    if not probs:
        return None
    return max(_OUTCOME_KEYS, key=lambda k: probs.get(k, 0.0))


def _confidence_bucket(probs: dict[str, float] | None) -> str | None:
    """Dry-run-only confidence bucket derived from the top probability.

    Deliberately simple and clearly labelled: it is not the productive
    confidence_band, only a consistent yardstick applied identically to the
    current and simulated distributions so a bucket change is comparable.
    """
    if not probs:
        return None
    top = max(probs.get(k, 0.0) for k in _OUTCOME_KEYS)
    if top >= 0.5:
        return "alta"
    if top >= 0.4:
        return "media"
    return "baja"


def _calibrated_to_outcomes(vector: dict[str, float] | None) -> dict[str, float] | None:
    if not vector:
        return None
    return {
        outcome: round(float(vector[src]), 6)
        for src, outcome in _VECTOR_TO_OUTCOME.items()
        if src in vector
    }


def build_activation_dry_run_payload(
    session: Session,
    links: list[ProgolSlateMatchModel],
    *,
    slate_id: str,
    draw_code: str | None,
) -> dict[str, Any]:
    """Core read-only builder working on a list of slate-match links."""
    _enforce_read_only_transaction(session)

    audit = audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=False,
        routing_policy=DRY_RUN_ROUTING_POLICY,
        calibrator_candidate_id=DRY_RUN_CALIBRATOR_CANDIDATE_ID,
        assume_calibrator_candidate_available=True,
    )
    gate_config = audit["gate_config"]
    latest_predictions = _latest_predictions_by_match(session)
    candidate = get_team_rating_calibrator_candidate(DRY_RUN_CALIBRATOR_CANDIDATE_ID)

    matches: list[dict[str, Any]] = []
    changed_top_pick = 0
    changed_bucket = 0
    max_delta = 0.0
    positions_changed_pick: list[int] = []

    for row in audit["rows"]:
        pred = latest_predictions.get(row["match_id"])
        current = (
            {
                "1": round(float(pred.home_probability), 6),
                "X": round(float(pred.draw_probability), 6),
                "2": round(float(pred.away_probability), 6),
            }
            if pred is not None
            else None
        )
        would_route = bool(row["would_use_rating_model"])
        calibrated = _calibrated_to_outcomes(row.get("calibrated_probability_vector"))
        fallback_used = "FALLBACK_USED" in row.get("legacy_sanity_flags", [])
        current_engine = "fallback" if fallback_used else "xgboost"

        dry: dict[str, float] | None
        if would_route and calibrated is not None and current is not None:
            dry = calibrated
            dry_run_engine = "team_rating_calibrated"
        else:
            dry = current
            dry_run_engine = current_engine

        delta: dict[str, float] | None = None
        max_abs_delta = 0.0
        if current is not None and dry is not None:
            delta = {k: round(dry.get(k, 0.0) - current.get(k, 0.0), 6) for k in _OUTCOME_KEYS}
            max_abs_delta = round(max(abs(v) for v in delta.values()), 6)
            max_delta = max(max_delta, max_abs_delta)

        current_pick = _top_pick(current)
        dry_pick = _top_pick(dry)
        pick_changed = bool(current_pick and dry_pick and current_pick != dry_pick)
        current_bucket = _confidence_bucket(current)
        dry_bucket = _confidence_bucket(dry)
        bucket_changed = bool(
            current_bucket and dry_bucket and current_bucket != dry_bucket
        )
        if pick_changed:
            changed_top_pick += 1
            positions_changed_pick.append(int(row["position"]))
        if bucket_changed:
            changed_bucket += 1

        warnings = list(row.get("warnings", []))
        warnings.append("dry_run_only")

        matches.append(
            {
                "position": row["position"],
                "match_id": row["match_id"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "competition": row["competition"],
                "current_engine": current_engine,
                "dry_run_engine": dry_run_engine,
                "would_route": would_route,
                "current_probabilities": current,
                "dry_run_probabilities": dry,
                "probability_delta": delta,
                "max_abs_delta": max_abs_delta,
                "current_top_pick": current_pick,
                "dry_run_top_pick": dry_pick,
                "top_pick_changed": pick_changed,
                "current_confidence_bucket": current_bucket,
                "dry_run_confidence_bucket": dry_bucket,
                "confidence_bucket_changed": bucket_changed,
                "blockers": list(row.get("blockers", [])),
                "warnings": warnings,
            }
        )

    s = audit["summary"]
    would_route = s["would_use_rating_model_if_enabled"]
    summary = {
        "total_matches": s["total_matches"],
        "eligible_if_enabled": s["eligible_if_enabled"],
        "would_route": would_route,
        "would_keep_current": s["total_matches"] - would_route,
        "blocked_by_rating": s["blocked_by_rating"],
        "blocked_by_review": s["blocked_by_review"],
        "blocked_by_hard_sanity": s["blocked_by_hard_sanity"],
        "changed_top_pick_count": changed_top_pick,
        "changed_confidence_bucket_count": changed_bucket,
        "max_probability_delta": round(max_delta, 6),
        "positions_would_route": s["positions_would_route"],
        "positions_changed_pick": sorted(positions_changed_pick),
    }

    compatible = bool(gate_config["calibrator_compatible"])
    activation_blockers: list[str] = []
    if not gate_config["team_rating_gate_enabled"]:
        activation_blockers.append("feature_flag_off")
    if not candidate.productive_available:
        activation_blockers.append("calibrator_productive_available_false")
    if not compatible:
        activation_blockers.append("calibrator_incompatible_scope")
    if summary["blocked_by_hard_sanity"]:
        activation_blockers.append("hard_sanity_blockers_present")

    return {
        "slate_id": slate_id,
        "draw_code": draw_code,
        "mode": "activation_dry_run",
        "production_active": False,
        "safe_to_activate": not activation_blockers,
        "dry_run_probability_model": DRY_RUN_PROBABILITY_MODEL,
        "activation_policy": {
            "competition_allowlist": list(settings.team_rating_gate_competitions),
            "routing_policy": DRY_RUN_ROUTING_POLICY,
            "calibrator_candidate": DRY_RUN_CALIBRATOR_CANDIDATE_ID,
            "temperature": candidate.temperature,
            "require_both_medium_plus": settings.team_rating_gate_require_both_medium_plus,
            "require_calibrator_compatible": True,
            "review_blocks": True,
        },
        "calibrator": {
            "id": candidate.candidate_id,
            "temperature": candidate.temperature,
            "productive_available": candidate.productive_available,
            "compatible": compatible,
            "compatibility_blockers": list(
                gate_config["calibrator_compatibility_blockers"]
            ),
        },
        "summary": summary,
        "matches": matches,
        "activation_blockers": activation_blockers,
    }


def build_slate_activation_dry_run(
    session: Session, slate: ProgolSlateModel
) -> TeamRatingActivationDryRunResponse:
    links = sorted(slate.matches, key=lambda link: link.position)
    payload = build_activation_dry_run_payload(
        session,
        links,
        slate_id=slate.id,
        draw_code=getattr(slate, "draw_code", None),
    )
    return TeamRatingActivationDryRunResponse(**payload)
