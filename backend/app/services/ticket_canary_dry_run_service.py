"""R5.7 — Ticket/Optimizer dry-run using canary effective probabilities.

Strictly read-only and in-memory. Builds TWO ticket recommendations for a slate
and diffs them:

* **current** — the ticket the system recommends today (display/decision probs),
* **canary**  — the ticket it *would* recommend if the optimizer consumed the
  canary ``effective_decision_probabilities`` for canary-active positions.

It NEVER activates the real ticket, never integrates the optimizer with the
canary, and never writes a row: it reuses ``TicketRecommendationService
.build_read_only`` (no snapshot), ``PredictionService.build_slate_predictions
(persist_audit=False)`` and ``FeatureService.build_match_features(persist=False)``.
Sanity-driven guardrails (``_allows_confident_single`` / presentation guard) are
preserved in BOTH tickets, so a "no dejar simple" match never becomes a
confident single in either.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.repositories.entity_repository import EntityRepository
from app.repositories.feature_repository import FeatureRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.repositories.training_repository import TrainingRepository
from app.schemas.prediction import MatchPredictionResponse
from app.services.feature_service import FeatureService
from app.services.diagnostic_ttl_cache import cached_diagnostic_report
from app.services.model_training_service import ModelTrainingService
from app.services.prediction_service import PredictionService
from app.services.team_rating_canary_service import apply_canary_to_predictions
from app.services.ticket_recommendation_service import TicketRecommendationService

_PICK_TYPE_LABEL = {"fixed": "simple", "double": "double", "triple": "triple"}
# The representative ticket coverage mode used for the per-match diff: the
# optimizer's recommended doubles plan (simples + chosen doubles).
_REPRESENTATIVE_MODE = "doubles"


def _pick_label(pick_type: str) -> str:
    return _PICK_TYPE_LABEL.get(pick_type, pick_type)


def _canary_probability_copy(pred: MatchPredictionResponse) -> MatchPredictionResponse:
    """Return a copy whose decision/display vectors are the canary effective
    vector — but ONLY for canary-active positions. Sanity fields are untouched.
    """
    canary = pred.canary
    if not (canary and canary.active):
        return pred
    eff = pred.effective_decision_probabilities or pred.effective_probabilities
    if not eff or not ({"L", "E", "V"} <= set(eff)):
        return pred
    eff = {k: float(v) for k, v in eff.items()}
    return pred.model_copy(
        update={
            "decision_probabilities": dict(eff),
            "display_probabilities": dict(eff),
            "probabilities": dict(eff),
            "home_probability": eff["L"],
            "draw_probability": eff["E"],
            "away_probability": eff["V"],
        }
    )


def _mode_pick(recommendation: Any, simple_allowed: bool) -> tuple[str, list[str]]:
    """Effective per-match treatment under the representative (doubles) plan.

    A guard-blocked match (presentation_guard.simple_allowed=False) is NEVER
    reported as a confident simple even when the optimizer left it as a single
    (budget went elsewhere): it surfaces as ``no_simple`` (requires coverage),
    so a "no dejar simple" pick can never read as simple.
    """
    decision = recommendation.decisions.get(_REPRESENTATIVE_MODE)
    if decision is None:
        decision = recommendation.decisions.get("simple")
    pick_type = _pick_label(decision.pick_type)
    picks = [p.value if hasattr(p, "value") else str(p) for p in decision.picks]
    if pick_type == "simple" and not simple_allowed:
        pick_type = "no_simple"
    return pick_type, picks


def _coverage_for_mode(coverage: list[Any], mode: str) -> dict[str, Any]:
    entry = next((c for c in coverage if c.mode == mode), None)
    if entry is None:
        return {}
    return {
        "expected_correct": entry.expected_correct,
        "target_floor": entry.target_floor,
        "target_probability": entry.target_probability,
        "target_met": entry.target_met,
        "jackpot_probability": entry.jackpot_probability,
    }


def _counts(pick_types: list[str]) -> dict[str, int]:
    out = {"simple_count": 0, "no_simple_count": 0, "double_count": 0, "triple_count": 0}
    for pick_type in pick_types:
        if pick_type == "simple":
            out["simple_count"] += 1
        elif pick_type == "no_simple":
            out["no_simple_count"] += 1
        elif pick_type == "double":
            out["double_count"] += 1
        elif pick_type == "triple":
            out["triple_count"] += 1
    return out


def build_ticket_canary_dry_run(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Build the read-only current-vs-canary ticket comparison for one slate."""
    key = (
        slate.id,
        slate.composition_hash,
        slate.slate_version,
        len(slate.matches),
    )
    return cached_diagnostic_report(
        "ticket_canary_dry_run",
        key,
        lambda: _build_ticket_canary_dry_run_uncached(session, slate),
    )


def _build_ticket_canary_dry_run_uncached(
    session: Session, slate: ProgolSlateModel
) -> dict[str, Any]:
    training_service = ModelTrainingService(
        TrainingRepository(session), EntityRepository(session), ResultRepository(session)
    )
    prediction_service = PredictionService(training_service)
    predictions = prediction_service.build_slate_predictions(slate, persist_audit=False)
    plan = apply_canary_to_predictions(session, slate, predictions)

    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
    feature_payloads_by_match: dict[str, dict[str, Any]] = {}
    for slate_match in sorted(slate.matches, key=lambda item: item.position):
        _m, payload, _g = feature_service.build_match_features(slate_match.match.id, persist=False)
        feature_payloads_by_match[slate_match.match.id] = payload

    ticket_service = TicketRecommendationService(TicketRecommendationRepository(session))
    current = ticket_service.build_read_only(
        slate=slate, predictions=predictions, feature_payloads_by_match=feature_payloads_by_match
    )
    canary_predictions = [_canary_probability_copy(p) for p in predictions]
    canary = ticket_service.build_read_only(
        slate=slate, predictions=canary_predictions, feature_payloads_by_match=feature_payloads_by_match
    )

    current_by_match = {r.match_id: r for r in current.recommendations}
    canary_by_match = {r.match_id: r for r in canary.recommendations}

    matches: list[dict[str, Any]] = []
    changed_positions: list[int] = []
    simple_removed_positions: list[int] = []
    new_double_positions: list[int] = []
    new_triple_positions: list[int] = []
    current_pick_types: list[str] = []
    canary_pick_types: list[str] = []

    for pred in sorted(predictions, key=lambda p: p.position):
        cur_rec = current_by_match.get(pred.match_id)
        can_rec = canary_by_match.get(pred.match_id)
        if cur_rec is None or can_rec is None:
            continue
        guard = pred.presentation_guard
        simple_allowed = bool(guard.simple_allowed) if guard else False
        cur_type, cur_sel = _mode_pick(cur_rec, simple_allowed)
        can_type, can_sel = _mode_pick(can_rec, simple_allowed)
        current_pick_types.append(cur_type)
        canary_pick_types.append(can_type)
        changed = cur_type != can_type or cur_sel != can_sel
        pos = pred.position
        if changed:
            changed_positions.append(pos)
        if cur_type == "simple" and can_type != "simple":
            simple_removed_positions.append(pos)
        if cur_type != "double" and can_type == "double":
            new_double_positions.append(pos)
        if cur_type != "triple" and can_type == "triple":
            new_triple_positions.append(pos)

        reasons: list[str] = list(guard.reason) if guard is not None else []
        canary_active = bool(pred.canary and pred.canary.active)
        if canary_active and changed:
            reasons.append("canary_softened_confidence")

        matches.append(
            {
                "position": pos,
                "match": f"{pred.home_team_name} vs {pred.away_team_name}",
                "canary_active": canary_active,
                "current_pick_type": cur_type,
                "current_selection": cur_sel,
                "canary_pick_type": can_type,
                "canary_selection": can_sel,
                "changed": changed,
                "display_probabilities": dict(pred.display_probabilities),
                "effective_probabilities": dict(pred.effective_probabilities),
                "presentation_guard": {
                    "simple_allowed": bool(guard.simple_allowed) if guard else False,
                    "recommendation_label": guard.recommendation_label if guard else "NO SIMPLE",
                },
                "reason": list(dict.fromkeys(reasons)),
            }
        )

    current_counts = _counts(current_pick_types)
    canary_counts = _counts(canary_pick_types)
    cur_cov = _coverage_for_mode(current.coverage, _REPRESENTATIVE_MODE)
    can_cov = _coverage_for_mode(canary.coverage, _REPRESENTATIVE_MODE)

    # Risk proxy: more bare singles (confident + uncovered) == higher risk; the
    # canary softens confidence, so it tends to add coverage (fewer singles ==
    # lower risk == more conservative ticket).
    cur_singles = current_counts["simple_count"] + current_counts["no_simple_count"]
    can_singles = canary_counts["simple_count"] + canary_counts["no_simple_count"]
    if can_singles < cur_singles:
        risk_delta = "lower"
    elif can_singles > cur_singles:
        risk_delta = "higher"
    elif changed_positions:
        risk_delta = "mixed"
    else:
        risk_delta = "same"

    return {
        "mode": "ticket_canary_dry_run",
        "production_active": False,
        "ticket_integration_active": False,
        "optimizer_active": False,
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "match_count": len(slate.matches),
        },
        "summary": {
            "current_ticket": {**current_counts, "coverage_estimate": cur_cov},
            "canary_ticket": {**canary_counts, "coverage_estimate": can_cov},
            "changed_positions": changed_positions,
            "simple_removed_positions": simple_removed_positions,
            "new_double_positions": new_double_positions,
            "new_triple_positions": new_triple_positions,
            "canary_active_positions": list(plan.active_positions),
            "ticket_changed": bool(changed_positions),
            "risk_delta": risk_delta,
        },
        "matches": matches,
        "write_safety": {"writes_performed": False, "snapshot_created": False},
    }


def build_active_slates_ticket_canary_dry_run(session: Session) -> dict[str, Any]:
    """Dry-run for every active/upcoming slate (active_upcoming scope)."""
    from app.repositories.slate_repository import SlateRepository
    from app.services.active_slate_scope import build_active_slate_scope
    from app.services.slate_service import SlateService

    scope = build_active_slate_scope(session)
    slate_service = SlateService(SlateRepository(session))
    slates_out: list[dict[str, Any]] = []
    for info in scope:
        slate = slate_service.get_slate(info.slate_id)
        if slate is None:
            continue
        slates_out.append(build_ticket_canary_dry_run(session, slate))
    return {
        "mode": "ticket_canary_dry_run_active_upcoming",
        "production_active": False,
        "ticket_integration_active": False,
        "optimizer_active": False,
        "slate_count": len(slates_out),
        "slates": slates_out,
        "write_safety": {"writes_performed": False, "snapshot_created": False},
    }
