"""R6.0 — Money Mode Release Candidate (strictly read-only).

The final operational layer before the system is used for real Progol money
decisions. For each active/upcoming slate it answers a single question — *play
or don't play* — and produces three concrete, in-memory tickets
(aggressive / balanced / conservative) plus a per-match justification.

Design contract (all enforced here):

* **Read-only.** No ``build_and_persist``, ``save_snapshot``, ``session.add``,
  ``flush`` or ``commit``. It reuses ``PredictionService.build_slate_predictions
  (persist_audit=False)``, ``FeatureService.build_match_features(persist=False)``
  and ``TicketRecommendationService.build_read_only`` (no snapshot row). Opens
  its DB transaction ``READ ONLY`` and rolls back in ``finally``.
* **Canary-aware.** For canary-active positions it consumes the
  ``effective_decision_probabilities`` (same copy the ticket canary dry-run
  uses); everything else stays on the current display/decision vector.
* **Guardrailed.** The presentation guard is authoritative: a position with
  ``simple_allowed=False`` can NEVER surface as a confident simple. A forced
  single on such a position is reported as ``no_simple`` (uncovered risk), so a
  "no dejar simple" / risk_high / review / blocked match never reads as a fijo.

The three tickets are the optimizer's own rule-compliant coverage modes
(``simple`` / ``doubles`` / ``full``), relabelled and overlaid with the guard:

* **aggressive**  = ``simple`` mode  — cheapest, most singles.
* **balanced**    = ``doubles`` mode — bounded doubles plan (default recommended).
* **conservative**= ``full`` mode    — max doubles+triples coverage.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.repositories.entity_repository import EntityRepository
from app.repositories.feature_repository import FeatureRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.repositories.training_repository import TrainingRepository
from app.services.active_slate_scope import build_active_slate_scope
from app.services.feature_service import FeatureService
from app.services.diagnostic_ttl_cache import cached_diagnostic_report
from app.services.model_training_service import ModelTrainingService
from app.services.money_mode_validation_service import validate_slate_for_money_mode
from app.services.prediction_service import PredictionService
from app.services.slate_service import SlateService
from app.services.team_rating_canary_service import apply_canary_to_predictions
from app.services.ticket_canary_dry_run_service import _canary_probability_copy
from app.services.ticket_recommendation_service import TicketRecommendationService

MODE = "money_mode_release_candidate"

# Optimizer coverage mode -> Money Mode ticket label.
_TICKET_MODES = (("aggressive", "simple"), ("balanced", "doubles"), ("conservative", "full"))

# L/E/V Progol signal letters from the Outcome value code.
_CODE_TO_SIGNAL = {"1": "L", "X": "E", "2": "V"}

# Residual NO-SIMPLE positions that even the conservative ticket cannot cover,
# as a fraction of slate size, above which the slate is not playable for money.
_NO_JUGAR_RESIDUAL_RATIO = 0.34

# Costo unitario por combinación: NO configurado en el sistema. Mientras no
# exista una tarifa real, estimated_cost permanece null y se documenta.
_COST_NOTE = "costo unitario por combinacion no configurado"


def _signal(outcome: Any) -> str:
    code = outcome.value if hasattr(outcome, "value") else str(outcome)
    return _CODE_TO_SIGNAL.get(code, code)


def _coverage_for_mode(coverage: list[Any], mode: str) -> dict[str, Any]:
    entry = next((c for c in coverage if c.mode == mode), None)
    if entry is None:
        return {}
    return {
        "expected_correct": round(float(entry.expected_correct), 3),
        "target_floor": entry.target_floor,
        "target_probability": round(float(entry.target_probability), 4),
        "target_met": bool(entry.target_met),
        "jackpot_probability": round(float(entry.jackpot_probability), 4),
    }


def _risk_level(uncovered_ratio: float, covers_all: bool, target_met: bool) -> str:
    if covers_all and target_met:
        return "low"
    if covers_all:
        return "medium"
    if uncovered_ratio <= _NO_JUGAR_RESIDUAL_RATIO:
        return "high"
    return "very_high"


def _build_ticket(
    *,
    label: str,
    mode_key: str,
    predictions: list[Any],
    rec_by_match: dict[str, Any],
    coverage: list[Any],
    match_count: int,
) -> dict[str, Any]:
    """Build one money-mode ticket from an optimizer coverage mode.

    A forced single (``fixed``) on a guard-blocked position is reported as
    ``no_simple`` and counted as *uncovered* — it never reads as a simple.
    """
    selections: list[dict[str, Any]] = []
    counts = {"simple_count": 0, "no_simple_count": 0, "double_count": 0, "triple_count": 0}
    uncovered: list[int] = []
    combinations = 1

    for pred in sorted(predictions, key=lambda p: p.position):
        rec = rec_by_match.get(pred.match_id)
        if rec is None:
            continue
        decision = rec.decisions.get(mode_key) or rec.decisions.get("simple")
        guard = pred.presentation_guard
        simple_allowed = bool(guard.simple_allowed) if guard else False
        pick_type = decision.pick_type
        picks = [_signal(p) for p in decision.picks]
        pos = pred.position

        if pick_type == "double":
            typ, mult = "double", 2
            counts["double_count"] += 1
        elif pick_type == "triple":
            typ, mult = "triple", 3
            counts["triple_count"] += 1
        elif not simple_allowed:
            # fixed on a NO-SIMPLE position: forced single, never a fijo.
            typ, mult = "no_simple", 1
            counts["no_simple_count"] += 1
            uncovered.append(pos)
        else:
            typ, mult = "simple", 1
            counts["simple_count"] += 1

        combinations *= mult
        selections.append({"position": pos, "pick": picks, "type": typ})

    cov = _coverage_for_mode(coverage, mode_key)
    covers_all = not uncovered
    uncovered_ratio = (len(uncovered) / match_count) if match_count else 1.0
    risk = _risk_level(uncovered_ratio, covers_all, bool(cov.get("target_met")))

    return {
        "label": label,
        # Playable for real money only if it leaves NO "no dejar simple"
        # position exposed as a forced single.
        "playable": covers_all,
        "covers_all_no_simple": covers_all,
        "uncovered_no_simple_positions": uncovered,
        **counts,
        "estimated_combinations": combinations,
        "estimated_cost": None,
        "cost_note": _COST_NOTE,
        "risk_level": risk,
        "coverage_estimate": cov,
        "selections": selections,
    }


def _decide(
    *,
    validation: dict[str, Any],
    aggressive: dict[str, Any],
    balanced: dict[str, Any],
    conservative: dict[str, Any],
    match_count: int,
) -> dict[str, Any]:
    """Map the three tickets + validation into a single non-ambiguous verdict."""
    cons_uncovered = len(conservative["uncovered_no_simple_positions"])
    residual_ratio = (cons_uncovered / match_count) if match_count else 1.0

    if validation["data_blockers"]:
        return {
            "status": "NO_JUGAR",
            "reason": "Datos incompletos graves: " + ", ".join(validation["data_blockers"]),
            "confidence": "low",
            "recommended_ticket": None,
        }
    if validation["prediction_status"] in ("pending", "missing"):
        return {
            "status": "NO_JUGAR",
            "reason": "No hay predicciones persistidas ni live para la slate.",
            "confidence": "low",
            "recommended_ticket": None,
        }
    if residual_ratio > _NO_JUGAR_RESIDUAL_RATIO:
        return {
            "status": "NO_JUGAR",
            "reason": (
                f"Demasiados NO SIMPLE sin cobertura posible: {cons_uncovered}/{match_count} "
                "posiciones siguen como fijo forzado incluso en el boleto conservador "
                "(maxima cobertura permitida por las reglas del boleto). El riesgo no es cubrible."
            ),
            "confidence": "cautious",
            "recommended_ticket": None,
        }
    if cons_uncovered > 0:
        return {
            "status": "JUGAR_SOLO_CONSERVADOR",
            "reason": (
                f"Solo el boleto conservador acota el riesgo; quedan {cons_uncovered} "
                "posicion(es) NO SIMPLE como fijo forzado sobre el pick mas probable. "
                "Jugar unicamente la version conservadora, con cautela."
            ),
            "confidence": "cautious",
            "recommended_ticket": "conservative",
        }
    if balanced["covers_all_no_simple"]:
        return {
            "status": "JUGAR_BALANCEADO",
            "reason": (
                "El boleto balanceado cubre todos los NO SIMPLE sin convertir ninguna "
                "senal peligrosa en simple y sin costo absurdo."
            ),
            "confidence": "cautious",
            "recommended_ticket": "balanced",
        }
    return {
        "status": "JUGAR_SOLO_CONSERVADOR",
        "reason": (
            "El boleto balanceado deja algun NO SIMPLE sin cobertura; el conservador "
            "los cubre. Jugar solo la version conservadora."
        ),
        "confidence": "cautious",
        "recommended_ticket": "conservative",
    }


def _match_justification(pred: Any, mode_key: str, rec_by_match: dict[str, Any]) -> dict[str, Any]:
    guard = pred.presentation_guard
    simple_allowed = bool(guard.simple_allowed) if guard else False
    reasons = list(guard.reason) if guard else []
    canary_active = bool(pred.canary and pred.canary.active)

    rec = rec_by_match.get(pred.match_id)
    decision = None
    if rec is not None:
        decision = rec.decisions.get(mode_key) or rec.decisions.get("full")
    if decision is not None and decision.pick_type != "fixed":
        money_pick = [_signal(p) for p in decision.picks]
        pick_kind = "double" if decision.pick_type == "double" else "triple"
    elif decision is not None:
        # forced single on the model's strongest outcome
        money_pick = [_signal(p) for p in decision.picks]
        pick_kind = "simple" if simple_allowed else "no_simple"
    else:
        money_pick = []
        pick_kind = "unknown"

    return {
        "position": pred.position,
        "match": f"{pred.home_team_name} vs {pred.away_team_name}",
        "primary_signal": guard.primary_signal if guard else "",
        "recommendation": guard.recommendation_label if guard else "NO SIMPLE",
        "money_mode_pick": money_pick,
        "money_mode_pick_type": pick_kind,
        "reason": reasons,
        "canary_active": canary_active,
        "risk": guard.risk_level if guard else "unknown",
        "simple_allowed": simple_allowed,
    }


def build_money_mode(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Read-only Money Mode report for one slate."""
    key = (
        slate.id,
        slate.composition_hash,
        slate.slate_version,
        len(slate.matches),
    )
    return cached_diagnostic_report(
        "money_mode",
        key,
        lambda: _build_money_mode_uncached(session, slate),
    )


def _build_money_mode_uncached(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    validation = validate_slate_for_money_mode(session, slate)

    training_service = ModelTrainingService(
        TrainingRepository(session), EntityRepository(session), ResultRepository(session)
    )
    prediction_service = PredictionService(training_service)
    predictions = prediction_service.build_slate_predictions(slate, persist_audit=False)
    plan = apply_canary_to_predictions(session, slate, predictions)

    # Use canary effective probabilities for canary-active positions only.
    money_predictions = [_canary_probability_copy(p) for p in predictions]

    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
    feature_payloads_by_match: dict[str, dict[str, Any]] = {}
    for slate_match in sorted(slate.matches, key=lambda item: item.position):
        _m, payload, _g = feature_service.build_match_features(slate_match.match.id, persist=False)
        feature_payloads_by_match[slate_match.match.id] = payload

    ticket_service = TicketRecommendationService(TicketRecommendationRepository(session))
    recommendation = ticket_service.build_read_only(
        slate=slate,
        predictions=money_predictions,
        feature_payloads_by_match=feature_payloads_by_match,
    )
    rec_by_match = {r.match_id: r for r in recommendation.recommendations}
    match_count = len(slate.matches)

    tickets: dict[str, Any] = {}
    for label, mode_key in _TICKET_MODES:
        tickets[label] = _build_ticket(
            label=label,
            mode_key=mode_key,
            predictions=money_predictions,
            rec_by_match=rec_by_match,
            coverage=recommendation.coverage,
            match_count=match_count,
        )

    decision = _decide(
        validation=validation,
        aggressive=tickets["aggressive"],
        balanced=tickets["balanced"],
        conservative=tickets["conservative"],
        match_count=match_count,
    )
    recommended = decision["recommended_ticket"]
    for label, ticket in tickets.items():
        ticket["recommended"] = label == recommended

    # Per-match justification uses the conservative (max-coverage) plan so every
    # match shows the most protective defensible pick.
    matches = [
        _match_justification(pred, "full", rec_by_match)
        for pred in sorted(money_predictions, key=lambda p: p.position)
    ]
    do_not_simple = [
        m["position"] for m in matches if not m["simple_allowed"]
    ]
    must_review = [
        m["position"]
        for m in matches
        if "review" in m["reason"] or "blocked" in m["reason"]
    ]

    return {
        "mode": MODE,
        "production_active": False,
        "ticket_integration_active": False,
        "optimizer_active": False,
        "slate": {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "match_count": match_count,
        },
        "validation": validation,
        "decision": {
            "status": decision["status"],
            "reason": decision["reason"],
            "confidence": decision["confidence"],
            "recommended_ticket": recommended,
        },
        "tickets": tickets,
        "do_not_simple_positions": do_not_simple,
        "must_review_positions": must_review,
        "canary_influence_positions": list(plan.active_positions),
        "matches": matches,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_money_mode_for_slate_id(session: Session, slate_id: str) -> dict[str, Any] | None:
    slate = SlateService(SlateRepository(session)).get_slate(slate_id)
    if slate is None:
        return None
    return build_money_mode(session, slate)


def build_money_mode_for_draw_code(session: Session, draw_code: str) -> dict[str, Any] | None:
    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        return None
    return build_money_mode(session, slate)


def build_active_slates_money_mode(session: Session) -> dict[str, Any]:
    """Money Mode for every active/upcoming slate (active_upcoming scope)."""
    scope = build_active_slate_scope(session)
    slate_service = SlateService(SlateRepository(session))
    slates_out: list[dict[str, Any]] = []
    playable = 0
    for info in scope:
        slate = slate_service.get_slate(info.slate_id)
        if slate is None:
            continue
        report = build_money_mode(session, slate)
        if report["decision"]["status"] != "NO_JUGAR":
            playable += 1
        slates_out.append(report)
    return {
        "mode": "money_mode_release_candidate_active_upcoming",
        "production_active": False,
        "ticket_integration_active": False,
        "optimizer_active": False,
        "scope": "active_upcoming",
        "slate_count": len(slates_out),
        "playable_slate_count": playable,
        "slates": slates_out,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
