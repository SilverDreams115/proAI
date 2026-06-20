"""Read-only audit of predictive signal quality for a Progol slate.

This is a *diagnostic*: it never writes, never regenerates predictions and
never invokes the model. It reads the PERSISTED latest-deterministic
predictions (one row per match, newest ``generated_at`` for the slate's
current ``composition_hash``) and reports, per match, the full signal
trace already stored on the row:

  * legacy probabilities (the model-adjusted home/draw/away columns),
  * raw / display / decision / optimizer vectors + flags + evidence/risk/
    status from ``sanity_audit_json``,
  * the anchors (feature vector) the prediction was built on,
  * fallback usage and the model artifact that produced it.

Usage::

    python backend/scripts/audit_prediction_signal.py --draw-code PG-2338
    python backend/scripts/audit_prediction_signal.py --slate-id <uuid>
    python backend/scripts/audit_prediction_signal.py --draw-code PG-2338 --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import ModelTrainingRunModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.repositories.slate_repository import SlateRepository
from app.services.jornada_scoring_service import JornadaScoringService
from app.services.sanity_service import (
    EXTREME_PROBABILITY_THRESHOLD,
    SUSPICIOUS_CLASS_FLOOR,
)

# Anchor keys whose value is a count of real data points. A value of 0.0 is
# the "no data" default — treated as a missing/default feature.
_ANCHOR_KEYS = (
    "home_recent_matches",
    "away_recent_matches",
    "head_to_head_matches",
    "evidence_count",
)


@dataclass
class MatchSignalRow:
    position: int
    match_id: str
    home: str
    away: str
    prediction_id: str | None
    model_artifact_id: str | None
    model_version: str | None
    generated_at: str | None
    predicted_outcome: str | None
    raw_probabilities: dict[str, float]
    decision_probabilities: dict[str, float]
    display_probabilities: dict[str, float]
    legacy_probabilities: dict[str, float]
    fallback_used: bool
    evidence_level: str | None
    risk_level: str | None
    final_status: str | None
    visible_confidence: str | None
    ticket_strategy: str | None
    sanity_flags: list[str]
    blocked_reason: str | None
    feature_vector: dict[str, Any]
    feature_null_count: int
    feature_default_count: int
    feature_source_summary: str
    raw_extreme: bool
    capped: bool
    suspicious: bool
    classification: str
    missing: bool = False  # True when no stored prediction exists for the match


def _vec(d: dict[str, Any] | None) -> dict[str, float]:
    d = d or {}
    return {k: float(d.get(k, 0.0) or 0.0) for k in ("L", "E", "V")}


def _classify(
    *,
    final_status: str | None,
    fallback_used: bool,
    evidence_level: str | None,
    suspicious: bool,
    raw_extreme: bool,
    h2h: float,
    evidence_count: float,
) -> str:
    """Single primary class per match, most-severe-first precedence."""
    if final_status == "BLOQUEADO":
        return "blocked_by_sanity"
    if suspicious or raw_extreme:
        return "suspicious_raw"
    if fallback_used and h2h == 0.0 and evidence_count == 0.0:
        return "fallback_only"
    if evidence_level == "low":
        return "needs_data"
    if final_status == "REVISAR" or evidence_level == "medium":
        return "weak_signal"
    return "usable_signal"


def _model_version(session: Session, artifact_id: str | None, cache: dict[str, str | None]) -> str | None:
    if not artifact_id:
        return None
    if artifact_id in cache:
        return cache[artifact_id]
    run = session.get(ModelTrainingRunModel, artifact_id)
    cache[artifact_id] = run.model_name if run is not None else None
    return cache[artifact_id]


def _build_row(
    session: Session,
    link: ProgolSlateMatchModel,
    prediction: Any,
    version_cache: dict[str, str | None],
) -> MatchSignalRow:
    match = link.match
    home, away = match.home_team.name, match.away_team.name
    if prediction is None:
        return MatchSignalRow(
            position=link.position, match_id=match.id, home=home, away=away,
            prediction_id=None, model_artifact_id=None, model_version=None,
            generated_at=None, predicted_outcome=None,
            raw_probabilities={}, decision_probabilities={}, display_probabilities={},
            legacy_probabilities={}, fallback_used=False, evidence_level=None,
            risk_level=None, final_status=None, visible_confidence=None,
            ticket_strategy=None, sanity_flags=[], blocked_reason=None,
            feature_vector={}, feature_null_count=0, feature_default_count=0,
            feature_source_summary="no stored prediction",
            raw_extreme=False, capped=False, suspicious=False,
            classification="needs_data", missing=True,
        )

    audit = json.loads(prediction.sanity_audit_json) if prediction.sanity_audit_json else {}
    anchors = json.loads(prediction.anchors_json) if prediction.anchors_json else {}

    raw = _vec(audit.get("raw_probabilities"))
    decision = _vec(audit.get("decision_probabilities"))
    display = _vec(audit.get("display_probabilities"))
    legacy = {
        "L": float(prediction.home_probability),
        "E": float(prediction.draw_probability),
        "V": float(prediction.away_probability),
    }
    flags = list(audit.get("sanity_flags") or [])
    fallback_used = bool(audit.get("fallback_used", False))
    evidence_level = audit.get("evidence_level")
    final_status = audit.get("final_status")

    # Feature/anchor accounting: a 0.0 count anchor is the no-data default.
    null_count = sum(1 for k in _ANCHOR_KEYS if anchors.get(k) is None)
    default_count = sum(
        1 for k in _ANCHOR_KEYS if anchors.get(k) is not None and float(anchors.get(k) or 0.0) == 0.0
    )
    present = [f"{k}={anchors.get(k)}" for k in _ANCHOR_KEYS if k in anchors]
    source_summary = (
        ", ".join(present)
        + f" | readiness={prediction.competition_readiness}"
    )

    raw_extreme = max(raw.values(), default=0.0) >= EXTREME_PROBABILITY_THRESHOLD
    capped = "EXTREME_PROBABILITY_CAPPED" in flags
    suspicious = (
        "SUSPICIOUS_CLASS_PROBABILITY" in flags
        or min(raw["L"], raw["V"]) <= SUSPICIOUS_CLASS_FLOOR
    )
    h2h = float(anchors.get("head_to_head_matches", 0.0) or 0.0)
    ev = float(anchors.get("evidence_count", 0.0) or 0.0)

    return MatchSignalRow(
        position=link.position,
        match_id=match.id,
        home=home,
        away=away,
        prediction_id=prediction.id,
        model_artifact_id=audit.get("model_artifact_id"),
        model_version=_model_version(session, audit.get("model_artifact_id"), version_cache),
        generated_at=prediction.generated_at.isoformat() if prediction.generated_at else None,
        predicted_outcome=prediction.recommended_outcome,
        raw_probabilities=raw,
        decision_probabilities=decision,
        display_probabilities=display,
        legacy_probabilities=legacy,
        fallback_used=fallback_used,
        evidence_level=evidence_level,
        risk_level=audit.get("risk_level"),
        final_status=final_status,
        visible_confidence=audit.get("visible_confidence"),
        ticket_strategy=audit.get("ticket_strategy"),
        sanity_flags=flags,
        blocked_reason=prediction.blocked_reason,
        feature_vector=anchors,
        feature_null_count=null_count,
        feature_default_count=default_count,
        feature_source_summary=source_summary,
        raw_extreme=raw_extreme,
        capped=capped,
        suspicious=suspicious,
        classification=_classify(
            final_status=final_status,
            fallback_used=fallback_used,
            evidence_level=evidence_level,
            suspicious=suspicious,
            raw_extreme=raw_extreme,
            h2h=h2h,
            evidence_count=ev,
        ),
    )


def _resolve_slate(session: Session, *, slate_id: str | None, draw_code: str | None) -> ProgolSlateModel:
    repo = SlateRepository(session)
    if slate_id:
        slate = repo.get_slate(slate_id)
        if slate is None:
            raise ValueError(f"slate {slate_id!r} not found")
        return slate
    if draw_code:
        found = repo.find_by_draw_code(draw_code)
        if found is None:
            raise ValueError(f"draw_code {draw_code!r} not found")
        return repo.get_slate(found.id)  # type: ignore[return-value]
    raise ValueError("pass --slate-id or --draw-code")


def build_signal_audit(
    session: Session, *, slate_id: str | None = None, draw_code: str | None = None
) -> dict[str, Any]:
    slate = _resolve_slate(session, slate_id=slate_id, draw_code=draw_code)
    comp_hash = slate.composition_hash or ""
    links = sorted(slate.matches, key=lambda link: link.position)
    match_ids = [link.match_id for link in links]

    # Latest-deterministic read: newest row per match for the CURRENT hash.
    scorer = JornadaScoringService(session)
    latest = scorer._latest_predictions(slate.id, comp_hash, match_ids)  # noqa: SLF001

    # Generation accounting (read-only): how many persisted batches exist
    # for the current hash. Each batch writes one row per match within a few
    # seconds, while distinct batches are minutes/hours apart — so we cluster
    # timestamps with a gap threshold instead of counting raw timestamps.
    generated_ats = sorted(
        session.scalars(
            select(PredictionModel.generated_at).where(
                PredictionModel.slate_id == slate.id,
                PredictionModel.composition_hash == comp_hash,
            )
        ).all()
    )
    generations = _cluster_generations(generated_ats, gap_seconds=60.0)

    version_cache: dict[str, str | None] = {}
    rows = [_build_row(session, link, latest.get(link.match_id), version_cache) for link in links]

    total = len(rows)
    present_rows = [r for r in rows if not r.missing]
    fallback_count = sum(1 for r in present_rows if r.fallback_used)
    anchor_total = len(_ANCHOR_KEYS) * max(len(present_rows), 1)
    null_total = sum(r.feature_null_count for r in present_rows)
    default_total = sum(r.feature_default_count for r in present_rows)

    summary = {
        "total_predictions": len(present_rows),
        "match_count": total,
        "missing_predictions": sum(1 for r in rows if r.missing),
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / total, 3) if total else 0.0,
        "low_evidence_count": sum(1 for r in present_rows if r.evidence_level == "low"),
        "blocked_count": sum(1 for r in present_rows if r.final_status == "BLOQUEADO"),
        "review_count": sum(1 for r in present_rows if r.final_status == "REVISAR"),
        "raw_extreme_count": sum(1 for r in present_rows if r.raw_extreme),
        "capped_count": sum(1 for r in present_rows if r.capped),
        "suspicious_count": sum(1 for r in present_rows if r.suspicious),
        "feature_null_rate": round(null_total / anchor_total, 3),
        "feature_default_rate": round(default_total / anchor_total, 3),
        "model_artifact_ids_seen": sorted({r.model_artifact_id for r in present_rows if r.model_artifact_id}),
        "prediction_generations_seen": len(generations),
        "latest_generation_count": len(present_rows),
        "classification_breakdown": _breakdown([r.classification for r in rows]),
    }

    return {
        "slate_id": slate.id,
        "draw_code": slate.draw_code,
        "composition_hash": comp_hash,
        "slate_version": slate.slate_version,
        "summary": summary,
        "rows": [asdict(r) for r in rows],
    }


def _cluster_generations(timestamps: list[Any], *, gap_seconds: float) -> list[Any]:
    """Cluster sorted timestamps into batches; return one marker per batch.

    A new batch starts when the gap to the previous timestamp exceeds
    ``gap_seconds`` (batches are minutes/hours apart; rows within a batch
    are a few seconds apart)."""
    if not timestamps:
        return []
    batches = [timestamps[0]]
    for prev, cur in zip(timestamps, timestamps[1:]):
        if (cur - prev).total_seconds() > gap_seconds:
            batches.append(cur)
    return batches


def _breakdown(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only predictive signal audit.")
    parser.add_argument("--slate-id")
    parser.add_argument("--draw-code")
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    args = parser.parse_args()

    with SessionLocal() as session:
        report = build_signal_audit(session, slate_id=args.slate_id, draw_code=args.draw_code)
        # Hard read-only guarantee.
        session.rollback()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
