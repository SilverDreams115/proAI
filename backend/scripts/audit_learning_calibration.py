"""R7.0 — Learning calibration audit CLI (read-only, never trains).

Measures Brier / log-loss / ECE / top-1 / top-2 over the comparable slates,
broken down by confidence band, guardrail status and competition, for each
probability vector (raw / display / decision / effective).

Usage::

    python -m scripts.audit_learning_calibration
    python -m scripts.audit_learning_calibration --json
"""
from __future__ import annotations

import argparse
import json

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.learning_calibration_service import build_calibration_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Learning calibration audit (R7.0, read-only).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = build_calibration_audit(session)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    def _line(label: str, m: dict) -> None:
        print(f"  [{label}] n={m['n']} brier={m['brier']} "
              f"logloss={m['logloss']} ece={m['ece']} "
              f"top1={m['top1_accuracy']} top2={m['top2_coverage']}")

    print(f"calibration audit (trains={report['trains']})")
    print(f"  comparable slates: {report['comparable_slate_count']} "
          f"({', '.join(report['comparable_slates']) or 'none'})")
    print(f"  scored positions: {report['scored_position_count']} "
          f"(samples={report['sample_count']} = sum over the 3 vectors)")
    print(f"  note: {report['note']}")

    print("\n  -- all available rows per vector (n differs; NOT comparable across rows) --")
    for vname, grouped in report["vectors"].items():
        overall = grouped["overall"]
        if overall["n"] == 0:
            continue
        _line(vname, overall)

    matched = report.get("matched_subset") or {}
    if matched.get("positions"):
        print(f"\n  -- matched subset: {matched['positions']} positions carrying all 3 vectors --")
        for vname, m in matched["vectors"].items():
            if m["n"]:
                _line(vname, m)

    coverage = report.get("audit_payload_coverage") or {}
    missing = coverage.get("slates_without_audit") or []
    partial = coverage.get("slates_partial_audit") or []
    if missing or partial:
        print("\n  -- guardrail-trace coverage --")
        if missing:
            print(f"  sin audit (raw/display no medibles): {', '.join(missing)}")
        if partial:
            print(f"  audit parcial: {', '.join(partial)}")
        print(f"  {coverage.get('reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
