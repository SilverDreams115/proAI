"""MS PDF watcher — detect guiamedia.pdf changes and activate Progol MS.

Wraps the existing ``SlateProposalService.observe_ms`` (single network fetch,
idempotent upsert — no duplicate proposals/slates) and adds:

  * provenance change detection by ``pdf_sha256`` (vs the last recorded one);
  * a status: unchanged | changed_valid | changed_invalid | parse_error;
  * activation of the existing MS slate when the PDF has an accepted cierre for
    THIS concurso or current fixtures with a stale/mismatched cierre block;
  * optional pre-close snapshot/prediction generation (never post-close);
  * watch diagnostics persisted on the proposal payload (no migration).

Never generates a retroactive (post-close) prediction, never touches Weekend.
When LN keeps publishing the wrong cierre block, the provisional window is
audited in the proposal payload and expires through the normal cierre archival
job.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.settings import settings
from app.models.tables import ProgolSlateModel, ProgolSlateProposalModel
from app.repositories.slate_repository import SlateRepository
from app.services.slate_proposal_service import SlateProposalService

logger = logging.getLogger(__name__)


def _latest_ms_proposal(session: Any) -> ProgolSlateProposalModel | None:
    return session.scalar(
        select(ProgolSlateProposalModel)
        .where(
            ProgolSlateProposalModel.week_type == "midweek",
            ProgolSlateProposalModel.source_name != "operator_date_override",
        )
        .order_by(ProgolSlateProposalModel.last_seen_at.desc())
        .limit(1)
    )


def _payload(proposal: ProgolSlateProposalModel | None) -> dict[str, Any]:
    if proposal is None:
        return {}
    try:
        return json.loads(proposal.payload_json or "{}")
    except (ValueError, TypeError):
        return {}


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _provisional_ms_close(
    proposal: ProgolSlateProposalModel,
    payload: dict[str, Any],
    *,
    now: datetime,
) -> datetime | None:
    """Bounded close for MS guides with current fixtures but stale cierre."""
    if payload.get("week_type") != "midweek":
        return None
    fixtures = payload.get("fixtures") or []
    if not isinstance(fixtures, list) or len(fixtures) < 9:
        return None
    block = payload.get("block_diagnostics") or {}
    if not block.get("rejected_close_block_draw_code"):
        return None
    first_seen = _aware(proposal.first_seen_at) or now
    close = first_seen + timedelta(days=max(0.25, float(settings.ms_pdf_provisional_active_days)))
    return close if close > now else None


def _mark_provisional_close(
    proposal: ProgolSlateProposalModel,
    payload: dict[str, Any],
    closes_at: datetime,
) -> None:
    proposal.registration_closes_at = closes_at
    payload["registration_closes_at"] = closes_at.isoformat()
    payload["registration_close_source"] = "provisional_ms_pdf_window"
    payload["provisional_close_window_days"] = settings.ms_pdf_provisional_active_days
    payload["extraction_confidence"] = "provisional"


def run_ms_pdf_watch(
    session: Any,
    *,
    force: bool = False,
    now: datetime | None = None,
    generate_prediction: bool = True,
    proposal_service: SlateProposalService | None = None,
) -> dict[str, Any]:
    """Run one watcher tick. Returns a diagnostics dict (also persisted)."""
    now = now or datetime.now(timezone.utc)
    svc = proposal_service or SlateProposalService(session)

    prev = _latest_ms_proposal(session)
    prev_payload = _payload(prev)
    prev_sha = prev_payload.get("pdf_sha256")
    prev_watch = prev_payload.get("watch") or {}

    proposal = svc.observe_ms()
    if proposal is None:
        # Fetch failed or the PDF didn't parse to >= 9 fixtures.
        result = {
            "last_ms_pdf_checked_at": now.isoformat(),
            "last_ms_pdf_sha256": prev_sha,
            "last_ms_pdf_changed_at": prev_watch.get("last_ms_pdf_changed_at"),
            "last_ms_pdf_status": "parse_error",
            "activated": False,
            "prediction_generated": False,
            "reason": "el PDF no se pudo descargar o parsear (>=9 fixtures)",
        }
        logger.info("ms_pdf_watch", extra={"event": "ms_pdf_watch", **result})
        return result

    payload = _payload(proposal)
    new_sha = payload.get("pdf_sha256")
    block = payload.get("block_diagnostics") or {}
    closes_at = _parse_iso(payload.get("registration_closes_at"))
    draw_code = payload.get("draw_code")
    changed = bool(force or new_sha != prev_sha)
    provisional_closes_at = _provisional_ms_close(proposal, payload, now=now)

    if not changed:
        status = "unchanged"
        reason = "PDF sin cambios (mismo sha256)"
    elif not payload.get("fixtures"):
        status = "parse_error"
        reason = "PDF cambió pero no se pudieron extraer fixtures"
    elif closes_at is not None and block.get("accepted_close_block"):
        status = "changed_valid"
        reason = f"PDF trae cierre válido del concurso {draw_code}"
    else:
        status = "changed_invalid"
        rej = block.get("rejected_close_block_draw_code")
        reason = (
            f"PDF cambió pero el cierre sigue siendo inválido para {draw_code}"
            + (f" (bloque pertenece al concurso {rej})" if rej else "")
        )

    activated = False
    prediction_generated = False
    activation_reason = reason

    activation_closes_at = closes_at
    activation_source = str(payload.get("registration_close_source") or "official_pdf_close")
    if activation_closes_at is None and provisional_closes_at is not None:
        activation_closes_at = provisional_closes_at
        activation_source = "provisional_ms_pdf_window"

    if activation_closes_at is not None and activation_closes_at > now:
        slate = _find_ms_slate(session, draw_code)
        if slate is not None:
            slate.registration_closes_at = activation_closes_at
            if slate.is_archived:
                slate.is_archived = False
            if activation_source == "provisional_ms_pdf_window":
                _mark_provisional_close(proposal, payload, activation_closes_at)
            session.add(slate)
            session.flush()
            activated = True
            activation_reason = (
                f"MS {slate.draw_code} activada con cierre provisional desde fixtures oficiales"
                if activation_source == "provisional_ms_pdf_window"
                else f"MS {slate.draw_code} activada desde PDF oficial"
            )
            logger.info(
                "ms_pdf_watch_activated",
                extra={"event": "ms_pdf_watch_activated", "draw_code": slate.draw_code,
                       "registration_closes_at": activation_closes_at.isoformat(),
                       "registration_close_source": activation_source},
            )
            if generate_prediction:
                prediction_generated = _maybe_generate_preclose(session, slate, now)
        else:
            if activation_source == "provisional_ms_pdf_window":
                _mark_provisional_close(proposal, payload, activation_closes_at)
            # No existing slate for this concurso → leave to auto-promote.
            activation_reason = (
                f"cierre {activation_source} {draw_code}; sin slate existente, auto-promote la creará"
            )
    elif status == "changed_valid" and (closes_at is None or closes_at <= now):
        activation_reason = "cierre válido pero ya pasó; no se activa ni se predice (no retroactivo)"
    elif closes_at is None and payload.get("fixtures"):
        activation_reason = reason + "; ventana provisional expirada o no aplicable"

    changed_at = now.isoformat() if changed else prev_watch.get("last_ms_pdf_changed_at")
    result = {
        "last_ms_pdf_checked_at": now.isoformat(),
        "last_ms_pdf_sha256": new_sha,
        "last_ms_pdf_changed_at": changed_at,
        "last_ms_pdf_status": status,
        "activated": activated,
        "prediction_generated": prediction_generated,
        "reason": activation_reason,
        "registration_close_source": activation_source if activated else None,
    }
    # Persist watch diagnostics on the (refreshed) proposal payload.
    payload["watch"] = result
    proposal.payload_json = json.dumps(payload, default=str)
    session.add(proposal)
    session.flush()
    logger.info("ms_pdf_watch", extra={"event": "ms_pdf_watch", **result})
    return result


def _find_ms_slate(session: Any, draw_code: Any) -> ProgolSlateModel | None:
    """Find the midweek slate for this concurso's trailing digits, if any."""
    import re

    digits = re.search(r"(\d+)$", str(draw_code or ""))
    if digits is None:
        return None
    target = digits.group(1)
    for slate in SlateRepository(session).list_slates():
        if slate.week_type != "midweek":
            continue
        m = re.search(r"(\d+)$", slate.draw_code or "")
        if m and m.group(1) == target:
            return slate
    return None


def _maybe_generate_preclose(session: Any, slate: ProgolSlateModel, now: datetime) -> bool:
    """Generate a pre-close snapshot/prediction when the slate is open and none
    exists for its composition_hash. Never post-close."""
    closes = slate.registration_closes_at
    if closes is not None and closes.tzinfo is None:
        closes = closes.replace(tzinfo=timezone.utc)
    if closes is None or closes <= now:
        return False  # closed → no retroactive prediction
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.feature_repository import FeatureRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.ticket_repository import TicketRecommendationRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.feature_service import FeatureService
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import PredictionService
    from app.services.ticket_recommendation_service import TicketRecommendationService

    ticket_repo = TicketRecommendationRepository(session)
    existing = ticket_repo.latest_for_slate(
        slate.id,
        composition_hash=getattr(slate, "composition_hash", None),
        model_version=TicketRecommendationService.MODEL_VERSION,
    )
    if existing is not None:
        return False  # snapshot already exists for this composition

    training_service = ModelTrainingService(
        TrainingRepository(session), EntityRepository(session), ResultRepository(session)
    )
    prediction_service = PredictionService(training_service)
    predictions = prediction_service.build_slate_predictions(slate, persist_audit=True)
    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
    feature_payloads: dict[str, dict[str, Any]] = {}
    for sm in sorted(slate.matches, key=lambda i: i.position):
        _m, fp, _g = feature_service.build_match_features(sm.match.id, persist=False)
        feature_payloads[sm.match.id] = fp
    TicketRecommendationService(ticket_repo).build_and_persist(
        slate=slate, predictions=predictions, feature_payloads_by_match=feature_payloads
    )
    logger.info(
        "ms_pdf_watch_preclose_prediction",
        extra={"event": "ms_pdf_watch_preclose_prediction", "draw_code": slate.draw_code},
    )
    return True


def latest_ms_pdf_watch_diagnostics(session: Any) -> dict[str, Any]:
    """Read the persisted watch diagnostics for the API/UI (empty if none)."""
    return _payload(_latest_ms_proposal(session)).get("watch") or {}
