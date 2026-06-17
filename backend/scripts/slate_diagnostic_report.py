"""Per-slate prediction diagnostic report.

Aggregates the guardrailed prediction output for a slate into a single
distribution report so an operator can spot systemic problems at a
glance — "everyone went to the visitor", "many near-zero class
probabilities", "FIJO on a low-evidence match".

Two input modes:

  * ``--base-url`` (default): pull the live predictions from the API.
  * ``--input file.json``: read a saved predictions payload (the JSON
    list returned by ``GET /api/predictions/slates/{id}``). Useful for
    offline auditing and for the regression fixtures.

The aggregation logic itself lives in
``app.services.sanity_service.build_slate_distribution_report`` so the
exact thresholds are shared with the runtime guardrails and the tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Make `app` importable when run as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sanity_service import (  # noqa: E402
    EvidenceLevel,
    FinalStatus,
    SlateMatchObservation,
    build_slate_distribution_report,
    decision_leaks_raw_probabilities,
)

_OUTCOME_TO_LABEL = {"1": "home", "X": "draw", "2": "away"}


def _vector(prediction: dict[str, Any], key: str) -> dict[str, float] | None:
    v = prediction.get(key)
    if isinstance(v, dict) and {"L", "E", "V"} <= set(v):
        return {k: float(v[k]) for k in ("L", "E", "V")}
    return None


def _observation_from_prediction(prediction: dict[str, Any]) -> SlateMatchObservation:
    # Prefer the explicit, non-positional L/E/V vector the sanity layer
    # now emits; fall back to the legacy positional fields for old payloads.
    probs = prediction.get("probabilities")
    if isinstance(probs, dict) and {"L", "E", "V"} <= set(probs):
        probabilities = {
            "home": float(probs["L"]),
            "draw": float(probs["E"]),
            "away": float(probs["V"]),
        }
    else:
        probabilities = {
            "home": float(prediction.get("home_probability", 0.0)),
            "draw": float(prediction.get("draw_probability", 0.0)),
            "away": float(prediction.get("away_probability", 0.0)),
        }
    recommended = _OUTCOME_TO_LABEL.get(str(prediction.get("recommended_outcome", "")), "home")
    try:
        evidence_level = EvidenceLevel(str(prediction.get("evidence_level", "low")))
    except ValueError:
        evidence_level = EvidenceLevel.LOW
    try:
        final_status = FinalStatus(str(prediction.get("final_status", "REVISAR")))
    except ValueError:
        final_status = FinalStatus.REVISAR
    return SlateMatchObservation(
        probabilities=probabilities,
        recommended_label=recommended,
        evidence_level=evidence_level,
        is_international_friendly=bool(prediction.get("is_international_friendly", False)),
        final_status=final_status,
    )


def _fetch(base_url: str, path: str, api_key: str | None) -> Any:
    request = urllib.request.Request(f"{base_url}{path}")
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_predictions_from_db(slate_id: str) -> list[dict[str, Any]]:
    """Reconstruct the prediction view from the persisted audit rows.

    Reads the latest PredictionModel row per match for the slate and
    rebuilds the prediction-shaped dict from ``sanity_audit_json`` (the
    durable decision-time trace). Rows without a sanity trace (pre-v18 /
    pre-sanity) are surfaced with raw=None so the report shows the gap
    instead of inventing a decision."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.tables import PredictionModel

    session = SessionLocal()
    try:
        rows = session.scalars(
            select(PredictionModel)
            .where(PredictionModel.slate_id == slate_id)
            .order_by(PredictionModel.generated_at.desc())
        ).all()
        latest_by_match: dict[str, PredictionModel] = {}
        for row in rows:
            latest_by_match.setdefault(row.match_id, row)

        predictions: list[dict[str, Any]] = []
        for row in latest_by_match.values():
            trace = json.loads(row.sanity_audit_json) if row.sanity_audit_json else {}
            match = row.match
            predictions.append(
                {
                    "match_id": row.match_id,
                    "home_team_name": getattr(getattr(match, "home_team", None), "name", "?"),
                    "away_team_name": getattr(getattr(match, "away_team", None), "name", "?"),
                    "recommended_outcome": row.recommended_outcome,
                    "evidence_level": trace.get("evidence_level", "low"),
                    "final_status": trace.get("final_status", "REVISAR"),
                    "risk_level": trace.get("risk_level", "high"),
                    "is_international_friendly": trace.get("is_international_friendly", False),
                    "flags": trace.get("sanity_flags", []),
                    "raw_probabilities": trace.get("raw_probabilities"),
                    "display_probabilities": trace.get("display_probabilities"),
                    "decision_probabilities": trace.get("decision_probabilities"),
                    "optimizer_probabilities": trace.get("optimizer_probabilities"),
                    "probabilities": trace.get("decision_probabilities"),
                    "model_artifact_id": trace.get("model_artifact_id"),
                    "sanity_policy_version": trace.get("sanity_policy_version"),
                }
            )
        return predictions
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("PROAI_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.getenv("PROAI_AUTH_API_KEY"))
    parser.add_argument("--slate-id", default=None, help="Slate id (defaults to the first active slate).")
    parser.add_argument("--input", default=None, help="Read a predictions JSON list from this file instead of the API.")
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Read the persisted audit trace from the predictions table (requires --slate-id).",
    )
    parser.add_argument("--fail-on-alarm", action="store_true", help="Exit non-zero when any distribution alarm fires.")
    args = parser.parse_args()

    if args.from_db:
        if not args.slate_id:
            raise SystemExit("--from-db requires --slate-id.")
        predictions = _load_predictions_from_db(args.slate_id)
        slate_label = f"{args.slate_id} (audit DB)"
    elif args.input:
        predictions = json.loads(Path(args.input).read_text(encoding="utf-8"))
        slate_label = args.input
    else:
        base_url = args.base_url.rstrip("/")
        slate_id = args.slate_id
        if slate_id is None:
            slates = _fetch(base_url, "/api/slates", args.api_key)
            if not slates:
                raise SystemExit("No active slates returned by the API.")
            slate_id = slates[0]["id"]
        predictions = _fetch(base_url, f"/api/predictions/slates/{slate_id}", args.api_key)
        slate_label = slate_id

    observations = [_observation_from_prediction(p) for p in predictions]
    report = build_slate_distribution_report(observations)

    # Per-match raw / display / decision / optimizer view + leak detection.
    # `optimizer_probabilities` == `decision_probabilities` by design: the
    # ticket optimizer consumes `decision_vector()`, which reads the
    # decision vector. If they ever diverge, that is itself the bug.
    per_match: list[dict[str, Any]] = []
    leak_alarms: list[str] = []
    for p in predictions:
        raw = _vector(p, "raw_probabilities")
        display = _vector(p, "display_probabilities") or _vector(p, "probabilities")
        decision = _vector(p, "decision_probabilities") or _vector(p, "probabilities")
        optimizer = decision  # the optimizer reads decision_probabilities
        name = f"{p.get('home_team_name')} vs {p.get('away_team_name')}"
        per_match.append(
            {
                "match": name,
                "raw_probabilities": raw,
                "display_probabilities": display,
                "decision_probabilities": decision,
                "optimizer_probabilities": optimizer,
                "status": p.get("final_status"),
                "risk_level": p.get("risk_level"),
                "flags": p.get("flags", []),
            }
        )
        if raw and display and decision and decision_leaks_raw_probabilities(raw, display, decision):
            leak_alarms.append(
                f"OPTIMIZER_USING_RAW_PROBABILITIES: {name} — decision={decision} sigue al raw "
                f"pese a que display lo degradó a {display}."
            )

    payload = {
        "slate": slate_label,
        **report.as_dict(),
        "per_match": per_match,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    all_alarms = list(report.alarms) + leak_alarms
    if all_alarms:
        for alarm in all_alarms:
            print(f"ALARM: {alarm}", file=sys.stderr)
        if args.fail_on_alarm:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
