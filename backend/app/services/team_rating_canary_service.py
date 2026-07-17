"""Controlled team-rating canary (R5.6-B).

This is the first phase that changes *served* behaviour, but only for an
explicitly scoped canary: a configured draw-code, a configured position
allowlist and the International-Friendlies competition. For those matches it
recalibrates the **served effective probabilities** with the approved
temperature candidate (T=2.22) and annotates the response with canary metadata.

It is still strictly non-destructive:

* the persisted prediction and the legacy/`display_`/`decision_` probability
  fields are never changed — only the additive `effective_*` / `canary` fields
  are populated,
* it writes no row (no session.add/flush/commit, no FeatureService persistence,
  no snapshots),
* it never touches the ticket optimizer / TicketRecommendationService, never
  regenerates predictions and never trains.

Gating reuses the audited dry-run (`build_activation_dry_run_payload`) so a
position only goes canary-active when the gate would actually route it
(both-medium-plus rating, no review/hard sanity blocker, rating coverage). The
whole layer is a no-op when ``team_rating_canary_enabled`` is False, so the
rollback is a single flag flip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.domain.team_rating_calibrator import apply_temperature_scaling
from app.domain.team_rating_calibrator import get_team_rating_calibrator_candidate
from app.models.tables import ProgolSlateModel
from app.schemas.prediction import MatchCanaryInfo
from app.schemas.prediction import MatchPredictionResponse
from app.services.team_rating_activation_dry_run_service import (
    build_activation_dry_run_payload,
)

CANARY_ENGINE = "team_rating_canary_temperature_v1"
_OUTCOME_KEYS = ("L", "E", "V")


@dataclass(frozen=True)
class CanaryPlan:
    enabled: bool
    in_scope: bool
    calibrator_id: str
    routing_policy: str
    competition_allowlist: list[str]
    temperature: float
    allowed_positions: list[int]
    active_positions: list[int]
    blocked_positions: list[int]
    # Slate-scope calibrator compatibility blockers from the audited dry-run
    # (e.g. mixed_competitions). Informative: positions are gated per-position,
    # but the status panel must never claim "enabled" without surfacing why
    # nothing (or only part) activates.
    calibrator_compatibility_blockers: list[str]


def _normalize(value: str) -> str:
    return value.strip().lower()


def _slate_in_canary_scope(session: Session, slate: ProgolSlateModel, draw_code: str | None) -> bool:
    """Decide whether a slate is in canary scope, by rule (not hardcoded).

    - ``draw_code_allowlist`` (default): the draw_code must be in the configured
      allowlist.
    - ``active_upcoming``: the slate must be active/upcoming; if an allowlist is
      also configured it further restricts to those draw_codes.

    Either way this only decides *scope*; per-position gating still runs the
    audited dry-run, so blockers are never ignored.
    """
    draw_codes = settings.team_rating_canary_draw_codes
    if settings.team_rating_canary_scope == "active_upcoming":
        from app.services.active_slate_scope import is_slate_active_upcoming

        if not is_slate_active_upcoming(session, slate):
            return False
        return (not draw_codes) or (bool(draw_code) and draw_code in draw_codes)
    # Default: draw_code allowlist.
    return bool(draw_code) and draw_code in draw_codes


def compute_canary_plan(session: Session, slate: ProgolSlateModel) -> CanaryPlan:
    """Decide, read-only, which positions are canary-active for this slate."""
    candidate = get_team_rating_calibrator_candidate(
        settings.team_rating_canary_calibrator_id
    )
    enabled = bool(settings.team_rating_canary_enabled) and bool(candidate.canary_allowed)
    draw_code = getattr(slate, "draw_code", None)
    in_scope = _slate_in_canary_scope(session, slate, draw_code)

    all_positions = sorted(link.position for link in slate.matches)
    allowed_positions = sorted(settings.team_rating_canary_positions)
    allow_comps = {_normalize(c) for c in settings.team_rating_canary_competition_allowlist}

    active_positions: list[int] = []
    compatibility_blockers: list[str] = []
    if enabled and in_scope:
        links = sorted(slate.matches, key=lambda link: link.position)
        dry_run = build_activation_dry_run_payload(
            session, links, slate_id=slate.id, draw_code=draw_code
        )
        compatibility_blockers = list(
            (dry_run.get("calibrator") or {}).get("compatibility_blockers", [])
        )
        would_route = {
            int(m["position"]): m
            for m in dry_run["matches"]
            if m["would_route"]
            and _normalize(m["competition"]) in allow_comps
        }
        active_positions = sorted(
            pos for pos in allowed_positions if pos in would_route
        )

    blocked_positions = sorted(p for p in all_positions if p not in set(active_positions))
    return CanaryPlan(
        enabled=enabled,
        in_scope=in_scope,
        calibrator_id=candidate.candidate_id,
        routing_policy=settings.team_rating_canary_routing_policy,
        competition_allowlist=list(settings.team_rating_canary_competition_allowlist),
        temperature=candidate.temperature,
        allowed_positions=allowed_positions,
        active_positions=active_positions,
        blocked_positions=blocked_positions,
        calibrator_compatibility_blockers=compatibility_blockers,
    )


def _top_pick(probs: dict[str, float]) -> str:
    return max(_OUTCOME_KEYS, key=lambda k: probs.get(k, 0.0))


def _inactive_info() -> MatchCanaryInfo:
    return MatchCanaryInfo(active=False, engine="current", applied=False)


def apply_canary_to_predictions(
    session: Session,
    slate: ProgolSlateModel,
    predictions: list[MatchPredictionResponse],
) -> CanaryPlan:
    """Populate ``effective_*`` and ``canary`` on every prediction in place.

    For canary-active positions the effective probabilities are the
    temperature-scaled served decision vector; for every other position they
    simply mirror the served display vector (so canary OFF == identity). Returns
    the plan so callers can build the status response without recomputing.
    """
    plan = compute_canary_plan(session, slate)
    candidate = get_team_rating_calibrator_candidate(plan.calibrator_id)
    active = set(plan.active_positions)

    for pred in predictions:
        display = dict(pred.display_probabilities)
        if plan.enabled and plan.in_scope and pred.position in active:
            scaled = apply_temperature_scaling(display, candidate.temperature)
            scaled = {k: round(float(v), 6) for k, v in scaled.items()}
            delta = {k: round(scaled.get(k, 0.0) - display.get(k, 0.0), 6) for k in _OUTCOME_KEYS}
            max_abs = round(max(abs(v) for v in delta.values()), 6)
            original_pick = _top_pick(display)
            effective_pick = _top_pick(scaled)
            pred.effective_probabilities = dict(scaled)
            pred.effective_decision_probabilities = dict(scaled)
            pred.canary = MatchCanaryInfo(
                active=True,
                engine=CANARY_ENGINE,
                applied=True,
                original_display_probabilities=dict(display),
                probability_delta=delta,
                max_abs_delta=max_abs,
                original_top_pick=original_pick,
                effective_top_pick=effective_pick,
                top_pick_changed=original_pick != effective_pick,
                ticket_uses_canary=False,
                warnings=["canary_active", "ticket_not_using_canary"],
            )
        else:
            pred.effective_probabilities = dict(display)
            pred.effective_decision_probabilities = dict(pred.decision_probabilities)
            pred.canary = _inactive_info()
    return plan


def build_canary_status(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    plan = compute_canary_plan(session, slate)
    return {
        "canary_enabled": plan.enabled,
        "scope": getattr(slate, "draw_code", None),
        "in_scope": plan.in_scope,
        "competition_allowlist": plan.competition_allowlist,
        "routing_policy": plan.routing_policy,
        "calibrator_id": plan.calibrator_id,
        "temperature": plan.temperature,
        "allowed_positions": plan.allowed_positions,
        "active_positions": plan.active_positions,
        "blocked_positions": plan.blocked_positions,
        "calibrator_compatibility_blockers": plan.calibrator_compatibility_blockers,
        "full_activation": False,
        "ticket_integration": False,
        "rollback_available": True,
    }
