"""Pure metric helpers extracted from ModelTrainingService.

`drift_severity` and `summarize_walk_forward` live here because they are
table-lookup / aggregation functions: zero database calls, zero logging,
zero dependency on the service's repositories. Pulling them out means
unit tests can stress the aggregation logic without standing up the
whole training stack, and the service file shrinks toward orchestration
only.
"""

from __future__ import annotations

from typing import Any, TypedDict


class WalkForwardThresholds(TypedDict):
    """The four ready-for-live-picks gates pulled from the service.

    Declared as a TypedDict so the caller's intent is documented at the
    call site — the service still owns the canonical values, but this
    module no longer has to import the mixin to read them.
    """

    hit_rate: float
    brier_score_max: float
    confident_hit_rate: float
    min_confident_picks: int


def drift_severity(psi: float) -> str:
    """Categorize a feature's PSI against the standard PSI bands."""
    if psi >= 0.25:
        return "significant"
    if psi >= 0.10:
        return "moderate"
    return "stable"


def summarize_walk_forward(
    *,
    selected_model_name: str,
    matches_considered: int,
    evaluated: int,
    hits: int,
    brier_total: float,
    log_loss_total: float,
    confident_picks: int,
    confident_hits: int,
    min_training_matches: int,
    confidence_threshold: float,
    thresholds: WalkForwardThresholds,
) -> dict[str, Any]:
    """Aggregate walk-forward counters into the final report dict.

    The verdict logic is:
      - too few confident picks → ``insufficient_confident_samples``
      - all four thresholds met → ``ready``
      - otherwise → ``not_ready``
    The caller supplies the thresholds so different model names can use
    different gates without this function needing to know them."""
    hit_rate = round(hits / evaluated, 4)
    brier_score = round(brier_total / evaluated, 4)
    log_loss = round(log_loss_total / evaluated, 4)
    confident_pick_rate = round(confident_picks / evaluated, 4)
    confident_pick_hit_rate = (
        round(confident_hits / confident_picks, 4) if confident_picks else 0.0
    )
    ready_for_live_picks = (
        hit_rate >= thresholds["hit_rate"]
        and brier_score <= thresholds["brier_score_max"]
        and confident_picks >= thresholds["min_confident_picks"]
        and confident_pick_hit_rate >= thresholds["confident_hit_rate"]
    )
    if confident_picks < thresholds["min_confident_picks"]:
        verdict = "insufficient_confident_samples"
    elif ready_for_live_picks:
        verdict = "ready"
    else:
        verdict = "not_ready"
    return {
        "model_name": selected_model_name,
        "evaluation_mode": "walk_forward",
        "matches_considered": matches_considered,
        "matches_evaluated": evaluated,
        "min_training_matches": min_training_matches,
        "confidence_threshold": confidence_threshold,
        "hit_rate": hit_rate,
        "brier_score": brier_score,
        "log_loss": log_loss,
        "confident_pick_rate": confident_pick_rate,
        "confident_pick_hit_rate": confident_pick_hit_rate,
        "ready_for_live_picks": ready_for_live_picks,
        "verdict": verdict,
        "thresholds": dict(thresholds),
    }
