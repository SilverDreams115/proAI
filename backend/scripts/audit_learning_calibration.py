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

    print(f"calibration audit (trains={report['trains']})")
    print(f"  comparable slates: {report['comparable_slate_count']} "
          f"({', '.join(report['comparable_slates']) or 'none'})")
    print(f"  samples: {report['sample_count']}")
    print(f"  note: {report['note']}")
    for vname, grouped in report["vectors"].items():
        overall = grouped["overall"]
        if overall["n"] == 0:
            continue
        print(f"  [{vname}] n={overall['n']} brier={overall['brier']} "
              f"logloss={overall['logloss']} ece={overall['ece']} "
              f"top1={overall['top1_accuracy']} top2={overall['top2_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
