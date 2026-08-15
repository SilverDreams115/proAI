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
from app.models.tables import (
    MatchLiveResultModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    ProgolSlateProposalModel,
)
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
    # An operator override outranks anything that is not an accepted PDF close.
    # It has to beat a provisional one, not merely a missing one: once
    # `_mark_provisional_close` stamps the synthetic window into the payload,
    # later ticks read it back as a normal `registration_closes_at` and it is
    # indistinguishable from a real cierre. PGM-806 sat on a fabricated close
    # for three days that way, while an override with the right date had been
    # recorded on day one and never consulted.
    if activation_source != "official_pdf_close" or activation_closes_at is None:
        override_closes_at = _operator_close_override(session, draw_code, now=now)
        if override_closes_at is not None:
            activation_closes_at = override_closes_at
            activation_source = "operator_date_override"
    if activation_closes_at is None and provisional_closes_at is not None:
        activation_closes_at = provisional_closes_at
        activation_source = "provisional_ms_pdf_window"

    if activation_closes_at is not None and activation_closes_at > now:
        slate = _find_ms_slate(session, draw_code)
        if slate is not None and _slate_has_started(session, slate, now):
            # The concurso has already kicked off / has observed results. A
            # started contest must not have its cierre pushed forward or be
            # un-archived by a provisional MS window (mirrors the
            # ``_has_observed_result`` guard in ``SlateService.is_closed``).
            activation_reason = (
                f"MS {slate.draw_code} ya inició (kickoff pasado o resultados "
                "observados); no se reactiva ni se mueve el cierre provisional"
            )
        elif slate is not None:
            # The provisional window is `first_seen + N days`, which knows
            # nothing about when the matches are. On PGM-809 that produced a
            # cierre on 19 Aug 19:30Z for a concurso whose first match kicks
            # off on the 18th at 19:00Z — a day in which the operator could
            # still "play" a slate already under way. An invented close may
            # never outlive the first kickoff; an official one is left alone,
            # because that is the contest's own word.
            if activation_source == "provisional_ms_pdf_window":
                first_kickoff = _first_kickoff(slate)
                if first_kickoff is not None and activation_closes_at > first_kickoff:
                    logger.info(
                        "ms_pdf_watch provisional close clamped to first kickoff",
                        extra={
                            "event": "ms_pdf_provisional_close_clamped",
                            "draw_code": slate.draw_code,
                            "window_close": activation_closes_at.isoformat(),
                            "first_kickoff": first_kickoff.isoformat(),
                        },
                    )
                    activation_closes_at = first_kickoff
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


def _first_kickoff(slate: ProgolSlateModel) -> datetime | None:
    """Earliest kickoff on the slate, or None when no fixture carries one."""
    kickoffs: list[datetime] = []
    for link in slate.matches or []:
        match = getattr(link, "match", None)
        kickoff = _aware(getattr(match, "kickoff_at", None) if match is not None else None)
        if kickoff is not None:
            kickoffs.append(kickoff)
    return min(kickoffs, default=None)


def _slate_has_started(session: Any, slate: ProgolSlateModel, now: datetime) -> bool:
    """True once the concurso has begun: a match has kicked off or any live/
    final result has been observed for its fixtures. A started concurso must
    not be reactivated with a future provisional cierre."""
    for link in slate.matches:
        match = getattr(link, "match", None)
        kickoff = getattr(match, "kickoff_at", None) if match is not None else None
        if kickoff is None:
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff <= now:
            return True
    if not slate.id:
        return False
    stmt = (
        select(MatchLiveResultModel.id)
        .join(
            ProgolSlateMatchModel,
            ProgolSlateMatchModel.match_id == MatchLiveResultModel.match_id,
        )
        .where(ProgolSlateMatchModel.slate_id == slate.id)
        .limit(1)
    )
    return session.scalar(stmt) is not None


def _operator_close_override(
    session: Any,
    draw_code: Any,
    *,
    now: datetime,
) -> datetime | None:
    """Return an operator-recorded cierre for this concurso, if one applies.

    Matched on the trailing digits, the same way ``_find_ms_slate`` matches
    slates: the PDF calls the concurso "806" while the operator records the
    override against "PGM-806", and an exact-string lookup silently misses it.

    Only a close still in the future is honoured — a stale override is worse
    than the provisional window, since it would archive the slate on sight.
    """
    import re

    digits = re.search(r"(\d+)$", str(draw_code or ""))
    if digits is None:
        return None
    target = digits.group(1)

    rows = session.scalars(
        select(ProgolSlateProposalModel)
        .where(
            ProgolSlateProposalModel.week_type == "midweek",
            ProgolSlateProposalModel.source_name == "operator_date_override",
            ProgolSlateProposalModel.registration_closes_at.is_not(None),
        )
        .order_by(ProgolSlateProposalModel.last_seen_at.desc())
    )
    for row in rows:
        m = re.search(r"(\d+)$", row.draw_code or "")
        if not m or m.group(1) != target:
            continue
        closes_at = row.registration_closes_at
        if closes_at is None:
            continue
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if closes_at > now:
            return closes_at
    return None


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
