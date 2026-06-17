"""Base-signal quality audit (diagnostic only — no product changes).

The sanity layer guardrails the *displayed/decided* probabilities, but this
report looks UNDERNEATH them at the model's BASE signal (raw argmax and the
features that produced it) so we can tell whether a pick is genuinely sound
or just being covered up by guardrails.

It is strictly read-only: it scores matches the same way the API does (no
retraining, no probability/logic changes) and emits per-match diagnostics
plus a slate-level summary. Missing data is reported as ``unknown`` — never
invented.

    python backend/scripts/signal_audit_report.py --slate-id <id>
    python backend/scripts/signal_audit_report.py --slate-id <id> --json

Diagnostic warnings (audit-only, NOT product flags):
  BASE_SIGNAL_SUSPICIOUS, RAW_ARGMAX_LOW_SUPPORT, RAW_EXTREME_WITH_LOW_EVIDENCE,
  SUSPICIOUS_HOME_UNDERDOG, SUSPICIOUS_AWAY_BIAS, FALLBACK_SIGNAL,
  FRIENDLY_EXTRAPOLATION, UNKNOWN_NEUTRALITY, LOW_TEAM_SAMPLE,
  CANONICALIZATION_RISK, RATING_DIFF_EXAGGERATED

Per-match output (text and --json):
  position, home, away, competition, base_pick, raw_argmax, decision_argmax,
  sign_changed, raw_probabilities, decision_probabilities, ticket_strategy,
  visible_confidence, model_source, fallback_used, evidence_level, risk_level,
  flags, home_sample_count, away_sample_count, home_rating, away_rating,
  rating_diff, base_signal_drivers, is_neutral_site, competition_readiness,
  canonical_home_id, canonical_away_id, signal_warnings.

Slate summary:
  total_matches, raw_pick_distribution, decision_pick_distribution,
  visitor_raw_share, visitor_decision_share, fallback_count,
  low_evidence_count, friendly_count, raw_extreme_count,
  suspicious_signal_count, matches_requiring_manual_review.

READ-ONLY: this report never writes the database. It scores in-memory the
same way the API does and disables the prediction-audit persistence for the
run. Integratable into a future "Diagnóstico" tab via the `--json` payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --- Named thresholds (no magic numbers) -----------------------------------
LOW_TEAM_SAMPLE_MIN = 3          # recent matches per side below this = thin
RAW_EXTREME_THRESHOLD = 0.75     # raw argmax at/above this = extreme
RATING_DIFF_EXAGGERATED = 250.0  # Elo gap above this = exaggerated
SUSPICIOUS_CLASS_FLOOR = 0.05    # a class at/below this is near-zero
DEFAULT_ELO = 1500.0

_OUTCOME_TO_LABEL = {"1": "L", "X": "E", "2": "V"}
_LEV_TO_NAME = {"L": "home", "E": "draw", "V": "away"}


def _argmax_lev(probs: dict[str, float]) -> str:
    return max(("L", "E", "V"), key=lambda k: float(probs.get(k, 0.0)))


def _safe(value: Any) -> Any:
    return value if value is not None else "unknown"


def _base_signal_features(feature_map: dict[str, float], rating_diff: float | None) -> list[str]:
    """Return the directional drivers of the base signal, strongest first.

    Positive favours HOME, negative favours AWAY (sign convention of the
    model's gap features)."""
    drivers: list[tuple[float, str]] = []

    def add(name: str, value: float, label: str) -> None:
        if abs(value) >= 0.05:
            side = "→L" if value > 0 else "→V"
            drivers.append((abs(value), f"{label} {value:+.2f} {side}"))

    add("form_gap", float(feature_map.get("form_gap", 0.0)), "forma")
    add("goal_balance_gap", float(feature_map.get("goal_balance_gap", 0.0)), "balance_goles")
    add("head_to_head_points_gap", float(feature_map.get("head_to_head_points_gap", 0.0)), "h2h_pts")
    add("rest_gap_days", float(feature_map.get("rest_gap_days", 0.0)) / 5.0, "descanso")
    home_adv = float(feature_map.get("home_advantage", 0.0))
    if abs(home_adv) >= 0.05:
        drivers.append((abs(home_adv), f"ventaja_local {home_adv:+.2f} →L"))
    if rating_diff is not None and abs(rating_diff) >= 1.0:
        side = "→L" if rating_diff > 0 else "→V"
        drivers.append((abs(rating_diff) / 400.0, f"rating_diff {rating_diff:+.0f} {side}"))

    drivers.sort(key=lambda item: item[0], reverse=True)
    return [text for _w, text in drivers[:4]] or ["sin drivers direccionales (vector ~neutro)"]


def compute_signal_warnings(
    *,
    raw: dict[str, float],
    raw_argmax: str,
    raw_max: float,
    evidence_level: str,
    fallback_used: bool,
    is_friendly: bool,
    home_sample: int,
    away_sample: int,
    rating_diff: float | None,
    is_neutral_site: str,
    canonicalization_risk: bool,
) -> list[str]:
    """Pure diagnostic-warning computation (unit-tested). Returns the list of
    audit warnings for one match, with ``BASE_SIGNAL_SUSPICIOUS`` prepended
    when two or more *structural* warnings fire."""
    warnings: list[str] = []
    if fallback_used:
        warnings.append("FALLBACK_SIGNAL")
    if home_sample < LOW_TEAM_SAMPLE_MIN or away_sample < LOW_TEAM_SAMPLE_MIN:
        warnings.append("LOW_TEAM_SAMPLE")
    if raw_argmax != "E" and min(home_sample, away_sample) < LOW_TEAM_SAMPLE_MIN:
        warnings.append("RAW_ARGMAX_LOW_SUPPORT")
    if raw_max >= RAW_EXTREME_THRESHOLD and evidence_level == "low":
        warnings.append("RAW_EXTREME_WITH_LOW_EVIDENCE")
    if raw_argmax == "V" and float(raw.get("V", 0.0)) >= RAW_EXTREME_THRESHOLD and (
        evidence_level == "low" or is_friendly
    ):
        warnings.append("SUSPICIOUS_AWAY_BIAS")
    if (
        rating_diff is not None
        and rating_diff > 0  # home rated higher
        and raw_argmax == "V"
        and float(raw.get("V", 0.0)) >= RAW_EXTREME_THRESHOLD
    ):
        warnings.append("SUSPICIOUS_HOME_UNDERDOG")
    if rating_diff is not None and abs(rating_diff) >= RATING_DIFF_EXAGGERATED:
        warnings.append("RATING_DIFF_EXAGGERATED")
    if is_friendly and fallback_used and min(home_sample, away_sample) < LOW_TEAM_SAMPLE_MIN:
        warnings.append("FRIENDLY_EXTRAPOLATION")
    if is_neutral_site == "unknown":
        warnings.append("UNKNOWN_NEUTRALITY")
    if canonicalization_risk:
        warnings.append("CANONICALIZATION_RISK")
    # Composite: 2+ structural warnings (the ubiquitous neutrality one excluded).
    structural = [w for w in warnings if w != "UNKNOWN_NEUTRALITY"]
    if len(structural) >= 2:
        warnings.insert(0, "BASE_SIGNAL_SUSPICIOUS")
    return warnings


def _audit_match(prediction, match, *, feature_service, training_service) -> dict[str, Any]:
    pred = prediction
    home_name = getattr(match.home_team, "name", None)
    away_name = getattr(match.away_team, "name", None)

    raw = pred.raw_probabilities or {}
    decision = pred.decision_probabilities or pred.probabilities or {}
    raw_argmax = _argmax_lev(raw)
    decision_argmax = _argmax_lev(decision)
    base_pick = _OUTCOME_TO_LABEL.get(str(pred.recommended_outcome.value
                                          if hasattr(pred.recommended_outcome, "value")
                                          else pred.recommended_outcome), "?")
    raw_max = max((float(raw.get(k, 0.0)) for k in ("L", "E", "V")), default=0.0)

    # Feature vector + ratings (read-only).
    try:
        feature_map = feature_service.build_model_features(match, cutoff=match.kickoff_at)
    except Exception:
        feature_map = {}
    home_sample = int(float(feature_map.get("home_recent_matches", 0.0)))
    away_sample = int(float(feature_map.get("away_recent_matches", 0.0)))

    artifact = training_service.latest_artifact() or {}
    ratings = artifact.get("ratings", {}) if isinstance(artifact, dict) else {}
    home_rating = float(ratings.get(home_name, DEFAULT_ELO)) if isinstance(ratings, dict) else None
    away_rating = float(ratings.get(away_name, DEFAULT_ELO)) if isinstance(ratings, dict) else None
    has_ratings = isinstance(ratings, dict) and bool(ratings)
    if not has_ratings:
        home_rating = away_rating = None
    rating_diff = (home_rating - away_rating) if (home_rating is not None and away_rating is not None) else None

    engine = "unknown"
    fn = getattr(training_service, "prediction_engine_for_match", None)
    if fn is not None:
        try:
            engine = fn(match)
        except Exception:
            engine = "unknown"

    is_friendly = bool(getattr(pred, "is_international_friendly", False))
    fallback_used = bool(getattr(pred, "fallback_used", False))
    evidence_level = str(getattr(pred, "evidence_level", "unknown"))
    risk_level = str(getattr(pred, "risk_level", "unknown"))
    flags = list(getattr(pred, "flags", []) or [])

    # is_neutral_site: no schema field exists -> honest unknown.
    is_neutral_site = "unknown"

    # Canonicalization sanity: missing id, empty/numeric name, or self-equal.
    def _name_suspicious(name: str | None, team_id: str | None) -> bool:
        if not name or not team_id:
            return True
        cleaned = str(name).strip()
        if not cleaned or cleaned.isdigit() or cleaned == str(team_id):
            return True
        return any(token in cleaned.lower() for token in ("tbd", "placeholder", "unknown", "??"))

    canonical_home_id = getattr(match, "home_team_id", None)
    canonical_away_id = getattr(match, "away_team_id", None)
    canonicalization_risk = _name_suspicious(home_name, canonical_home_id) or _name_suspicious(
        away_name, canonical_away_id
    )

    warnings = compute_signal_warnings(
        raw=raw,
        raw_argmax=raw_argmax,
        raw_max=raw_max,
        evidence_level=evidence_level,
        fallback_used=fallback_used,
        is_friendly=is_friendly,
        home_sample=home_sample,
        away_sample=away_sample,
        rating_diff=rating_diff,
        is_neutral_site=is_neutral_site,
        canonicalization_risk=canonicalization_risk,
    )

    return {
        "position": pred.position,
        "home": _safe(home_name),
        "away": _safe(away_name),
        "competition": _safe(getattr(pred, "competition_name", None)),
        "base_pick": base_pick,
        "raw_argmax": raw_argmax,
        "decision_argmax": decision_argmax,
        "sign_changed": raw_argmax != decision_argmax,
        "raw_probabilities": {k: round(float(raw.get(k, 0.0)), 3) for k in ("L", "E", "V")},
        "decision_probabilities": {k: round(float(decision.get(k, 0.0)), 3) for k in ("L", "E", "V")},
        "ticket_strategy": _safe(getattr(pred, "ticket_strategy", None)),
        "visible_confidence": _safe(getattr(pred, "visible_confidence", None)),
        "model_source": engine,
        "fallback_used": fallback_used,
        "evidence_level": evidence_level,
        "risk_level": risk_level,
        "flags": flags,
        "home_sample_count": home_sample,
        "away_sample_count": away_sample,
        "home_rating": _safe(round(home_rating, 1) if home_rating is not None else None),
        "away_rating": _safe(round(away_rating, 1) if away_rating is not None else None),
        "rating_diff": _safe(round(rating_diff, 1) if rating_diff is not None else None),
        "base_signal_drivers": _base_signal_features(feature_map, rating_diff),
        "is_neutral_site": is_neutral_site,
        "competition_readiness": _safe(getattr(pred, "competition_readiness", None)),
        "canonical_home_id": _safe(canonical_home_id),
        "canonical_away_id": _safe(canonical_away_id),
        "signal_warnings": warnings,
    }


def build_slate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)

    def dist(key: str) -> dict[str, int]:
        d = {"L": 0, "E": 0, "V": 0}
        for r in rows:
            d[r[key]] = d.get(r[key], 0) + 1
        return d

    raw_dist = dist("raw_argmax")
    dec_dist = dist("decision_argmax")
    fallback_count = sum(1 for r in rows if r["fallback_used"])
    low_evidence_count = sum(1 for r in rows if r["evidence_level"] == "low")
    friendly_count = sum(1 for r in rows if "FRIENDLY_EXTRAPOLATION" in r["signal_warnings"] or r["competition_readiness"] == "context_only")
    raw_extreme_count = sum(
        1 for r in rows if max(r["raw_probabilities"].values()) >= RAW_EXTREME_THRESHOLD
    )
    suspicious_count = sum(1 for r in rows if "BASE_SIGNAL_SUSPICIOUS" in r["signal_warnings"])
    manual_review = sum(
        1
        for r in rows
        if "BASE_SIGNAL_SUSPICIOUS" in r["signal_warnings"]
        or (r["fallback_used"] and r["evidence_level"] == "low")
    )
    return {
        "total_matches": total,
        "raw_pick_distribution": raw_dist,
        "decision_pick_distribution": dec_dist,
        "visitor_raw_share": round(raw_dist["V"] / total, 3) if total else 0.0,
        "visitor_decision_share": round(dec_dist["V"] / total, 3) if total else 0.0,
        "fallback_count": fallback_count,
        "low_evidence_count": low_evidence_count,
        "friendly_count": friendly_count,
        "raw_extreme_count": raw_extreme_count,
        "suspicious_signal_count": suspicious_count,
        "matches_requiring_manual_review": manual_review,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-id", required=True, help="Slate id to audit.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.feature_repository import FeatureRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.slate_repository import SlateRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.feature_service import FeatureService
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import PredictionService
    from app.services.slate_service import SlateService

    session = SessionLocal()
    try:
        slate = SlateService(SlateRepository(session)).get_slate(args.slate_id)
        if slate is None:
            raise SystemExit(f"Slate {args.slate_id} not found.")
        training_service = ModelTrainingService(
            TrainingRepository(session), EntityRepository(session), ResultRepository(session)
        )
        feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
        prediction_service = PredictionService(training_service)
        # READ-ONLY: scoring is in-memory; disable the audit-row persistence so
        # the diagnostic never writes the database.
        prediction_service._persist_prediction_audit = lambda *a, **k: None  # type: ignore[method-assign]
        predictions = prediction_service.build_slate_predictions(slate)
        match_by_id = {sm.match.id: sm.match for sm in slate.matches}

        rows = [
            _audit_match(
                pred,
                match_by_id[pred.match_id],
                feature_service=feature_service,
                training_service=training_service,
            )
            for pred in predictions
            if pred.match_id in match_by_id
        ]
        rows.sort(key=lambda r: r["position"])
        summary = build_slate_summary(rows)
    finally:
        session.close()

    if args.json:
        print(json.dumps({"slate_id": args.slate_id, "matches": rows, "summary": summary}, ensure_ascii=False, indent=2))
        return 0

    # Human-readable report.
    print(f"=== Auditoría de señal base · slate {args.slate_id} ===\n")
    for r in rows:
        print(f"#{r['position']:>2} {r['home']} vs {r['away']}  [{r['competition']}]")
        print(f"     base_pick={r['base_pick']}  raw_argmax={r['raw_argmax']}  decision_argmax={r['decision_argmax']}  sign_changed={r['sign_changed']}")
        print(f"     raw={r['raw_probabilities']}  decision={r['decision_probabilities']}")
        print(f"     model_source={r['model_source']}  fallback_used={r['fallback_used']}  evidence={r['evidence_level']}  risk={r['risk_level']}")
        print(f"     ticket_strategy={r['ticket_strategy']}  visible_confidence={r['visible_confidence']}")
        print(f"     samples L/V={r['home_sample_count']}/{r['away_sample_count']}  ratings L/V={r['home_rating']}/{r['away_rating']}  rating_diff={r['rating_diff']}  neutral={r['is_neutral_site']}")
        print(f"     drivers: {', '.join(r['base_signal_drivers'])}")
        print(f"     canonical ids: {r['canonical_home_id']} / {r['canonical_away_id']}")
        if r["signal_warnings"]:
            print(f"     ⚠ WARNINGS: {', '.join(r['signal_warnings'])}")
        print()

    print("=== Resumen del slate ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
