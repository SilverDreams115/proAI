"""R7.0 — Learning calibration audit (read-only, never trains).

Aggregates every scored position across the *comparable* slates and measures how
well the probabilities were calibrated: Brier, log-loss, ECE, top-1 accuracy and
top-2 coverage, broken down by confidence band, guardrail status (ready vs
revisar vs NO-SIMPLE) and competition (friendlies vs real). It reports each
probability vector separately — raw, display, decision and effective — so a
divergence between what the model produced and what was shown/decided is visible.

It strictly audits. It does not fit, calibrate or persist anything.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateModel
from app.services.learning_slate_scoring_service import (
    _audit_probs,
    _audit_value,
    _decision_probs,
    _sign,
)
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.services.slate_classification_service import classify_slate

_VECTORS = ("raw_probabilities", "display_probabilities", "decision_probabilities")
_LOGLOSS_EPS = 1e-12
_ECE_BINS = 10


@dataclass
class _Sample:
    probs: dict[str, float]
    actual: str
    band: str
    final_status: str
    is_friendly: bool
    competition: str
    # Identifies the scored position so the three vectors can be intersected.
    # `raw` and `display` only exist inside `sanity_audit_json`, which no
    # prediction carried before 2026-06-16, while `decision` reads columns
    # that have always been populated. Comparing their headline numbers
    # straight across therefore compares different populations — and not
    # randomly different: the slates missing the payload (PG-2336, PGM-799,
    # PGM-800) are three of the four best-scoring jornadas, so the subset
    # `raw` is measured on is worse than average and the comparison flatters
    # `decision`. `matched_subset` re-runs all three on the rows where all
    # three exist; that is the only apples-to-apples read.
    key: str = ""


def _metrics(samples: list[tuple[dict[str, float], str]]) -> dict[str, Any]:
    n = len(samples)
    if n == 0:
        return {"n": 0, "brier": None, "logloss": None, "top1_accuracy": None,
                "top2_coverage": None, "ece": None}
    brier = 0.0
    logloss = 0.0
    top1 = 0
    top2 = 0
    bins: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(_ECE_BINS)]  # conf_sum, correct, count
    for probs, actual in samples:
        for s in ("L", "E", "V"):
            brier += (probs.get(s, 0.0) - (1.0 if s == actual else 0.0)) ** 2
        logloss += -math.log(max(probs.get(actual, 0.0), _LOGLOSS_EPS))
        ranked = sorted(("L", "E", "V"), key=lambda k: probs.get(k, 0.0), reverse=True)
        if ranked[0] == actual:
            top1 += 1
        if actual in ranked[:2]:
            top2 += 1
        conf = probs.get(ranked[0], 0.0)
        idx = min(int(conf * _ECE_BINS), _ECE_BINS - 1)
        bins[idx][0] += conf
        bins[idx][1] += 1.0 if ranked[0] == actual else 0.0
        bins[idx][2] += 1.0
    ece = 0.0
    for conf_sum, correct, count in bins:
        if count > 0:
            ece += (count / n) * abs((correct / count) - (conf_sum / count))
    return {
        "n": n,
        "brier": round(brier / n, 4),
        "logloss": round(logloss / n, 4),
        "top1_accuracy": round(top1 / n, 4),
        "top2_coverage": round(top2 / n, 4),
        "ece": round(ece, 4),
    }


def _collect_samples(session: Session) -> dict[str, list[_Sample]]:
    """Gather per-vector samples across comparable slates."""
    from app.repositories.slate_repository import SlateRepository
    from app.services.learning_slate_scoring_service import LearningSlateScoringService
    from app.services.slate_service import SlateService

    service = SlateService(SlateRepository(session))
    slates: list[ProgolSlateModel] = service.list_slates(include_closed=True)
    scorer = LearningSlateScoringService(session)

    by_vector: dict[str, list[_Sample]] = {v: [] for v in _VECTORS}
    comparable_slates: list[str] = []

    for slate in slates:
        reality = classify_slate(session, slate)
        if not reality.comparable_with_results:
            continue
        match_ids = [sm.match_id for sm in slate.matches]
        canonical = CanonicalResultRepository(session).get_with_conflict_info(match_ids)
        # Require full, conflict-free coverage to count as comparable.
        full = all(
            mid in canonical and not canonical[mid].is_conflicting for mid in match_ids
        )
        if not full or not match_ids:
            continue
        comparable_slates.append(slate.draw_code)
        predictions = scorer._latest_predictions(slate, match_ids)
        comp_by_match = {
            sm.match_id: sm.match.competition.name for sm in slate.matches
        }
        for mid in match_ids:
            pred = predictions.get(mid)
            cr = canonical.get(mid)
            if pred is None or cr is None or cr.is_conflicting:
                continue
            actual = _sign(cr.result.result_code)
            if actual is None:
                continue
            flags = _audit_value(pred, "sanity_flags") or []
            is_friendly = "INTERNATIONAL_FRIENDLY" in flags if isinstance(flags, list) else False
            band = pred.confidence_band or "low"
            final_status = str(_audit_value(pred, "final_status") or "").upper()
            competition = comp_by_match.get(mid, "unknown")
            vectors: dict[str, dict[str, float] | None] = {
                "raw_probabilities": _audit_probs(pred, "raw_probabilities"),
                "display_probabilities": _audit_probs(pred, "display_probabilities"),
                "decision_probabilities": _decision_probs(pred),
            }
            for vname, probs in vectors.items():
                if probs:
                    by_vector[vname].append(
                        _Sample(
                            probs=probs,
                            actual=actual,
                            band=band,
                            final_status=final_status,
                            is_friendly=is_friendly,
                            competition=competition,
                            key=f"{slate.draw_code}:{mid}",
                        )
                    )

    by_vector["__comparable_slates__"] = comparable_slates  # type: ignore[assignment]
    return by_vector


def _audit_payload_coverage(session: Session) -> dict[str, Any]:
    """Per-slate count of predictions carrying the guardrail trace.

    Surfaced because a missing payload is invisible in the headline numbers —
    it shows up only as a smaller `n`, which reads like a smaller slate rather
    than an unmeasurable one.
    """
    from sqlalchemy import func, select

    from app.models.tables import PredictionModel

    rows = session.execute(
        select(
            ProgolSlateModel.draw_code,
            func.count(PredictionModel.id),
            func.count(PredictionModel.sanity_audit_json),
        )
        .join(PredictionModel, PredictionModel.slate_id == ProgolSlateModel.id)
        .group_by(ProgolSlateModel.draw_code)
    ).all()
    per_slate = {
        str(code): {"predictions": int(total), "with_audit": int(with_audit)}
        for code, total, with_audit in rows
    }
    missing = sorted(k for k, v in per_slate.items() if v["with_audit"] == 0)
    partial = sorted(
        k for k, v in per_slate.items()
        if 0 < v["with_audit"] < v["predictions"]
    )
    return {
        "per_slate": per_slate,
        "slates_without_audit": missing,
        "slates_partial_audit": partial,
        "reason": (
            "sanity_audit_json was added on 2026-06-16; predictions written "
            "before it carry no raw/display vector. It cannot be backfilled — "
            "re-scoring those matches would record today's model output as "
            "what was served then."
        ),
    }


def _grouped(samples: list[_Sample]) -> dict[str, Any]:
    def metrics_for(predicate) -> dict[str, Any]:
        sub = [(s.probs, s.actual) for s in samples if predicate(s)]
        return _metrics(sub)

    bands = {b: metrics_for(lambda s, b=b: s.band == b) for b in ("high", "medium", "low", "blocked")}
    ready = metrics_for(lambda s: s.final_status in ("LISTO", "FIJO"))
    revisar = metrics_for(lambda s: s.final_status == "REVISAR")
    no_simple = metrics_for(lambda s: s.final_status in ("REVISAR", "BLOQUEADO"))
    friendlies = metrics_for(lambda s: s.is_friendly)
    competition = metrics_for(lambda s: not s.is_friendly)
    by_competition = {
        comp: metrics_for(lambda s, c=comp: s.competition == c)
        for comp in sorted({s.competition for s in samples})
    }
    return {
        "overall": _metrics([(s.probs, s.actual) for s in samples]),
        "by_confidence_band": bands,
        "ready": ready,
        "revisar": revisar,
        "no_simple": no_simple,
        "friendlies": friendlies,
        "competition_real": competition,
        "by_competition": by_competition,
    }


def build_calibration_audit(session: Session) -> dict[str, Any]:
    by_vector = _collect_samples(session)
    comparable_slates = by_vector.pop("__comparable_slates__", [])  # type: ignore[arg-type]
    vectors_report = {
        vname: _grouped(samples) for vname, samples in by_vector.items()
    }
    total = sum(len(s) for s in by_vector.values())

    # Same positions, all three vectors — the comparison the headline numbers
    # cannot support while their `n` differs.
    shared = set.intersection(
        *({s.key for s in samples} for samples in by_vector.values())
    ) if all(by_vector.values()) else set()
    matched = {
        vname: _metrics(
            [(s.probs, s.actual) for s in samples if s.key in shared]
        )
        for vname, samples in by_vector.items()
    }

    return {
        "mode": "learning_calibration_audit",
        "trains": False,
        "comparable_slates": comparable_slates,
        "comparable_slate_count": len(comparable_slates),
        # Positions counted once, unlike `sample_count`, which sums the three
        # vectors and so reads ~3x larger than the number of scored matches.
        "scored_position_count": len({s.key for s in by_vector["decision_probabilities"]}),
        "sample_count": total,
        "matched_subset": {
            "positions": len(shared),
            "vectors": matched,
            "note": (
                "all three vectors on the identical positions; the only "
                "valid raw-vs-decision comparison"
            ),
        },
        "audit_payload_coverage": _audit_payload_coverage(session),
        "vectors": vectors_report,
        "note": (
            "read-only calibration audit; no calibrator is fitted or persisted"
            if total
            else "no comparable samples yet — calibration blocked until official results exist"
        ),
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
