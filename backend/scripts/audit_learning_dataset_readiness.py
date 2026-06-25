"""R7.0 — Learning dataset readiness audit CLI (read-only, never trains).

Reports whether there is enough clean, comparable evidence to justify training
or adjusting a model. Never marks training_ready=true while results are missing
or conflicts are high.

Usage::

    python -m scripts.audit_learning_dataset_readiness
    python -m scripts.audit_learning_dataset_readiness --json
"""
from __future__ import annotations

import argparse
import json

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.learning_dataset_readiness_service import build_dataset_readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Learning dataset readiness (R7.0, read-only).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = build_dataset_readiness(session)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    print(f"dataset readiness (trains={report['trains']})")
    print(f"  training_ready: {report['training_ready']}")
    print(f"  reason: {report['reason']}")
    print(f"  comparable slates: {report['comparable_slate_count']} "
          f"({', '.join(report['comparable_slates']) or 'none'})")
    print(f"  comparable matches: {report['comparable_match_count']} "
          f"(conflicts={report['conflict_match_count']}, ratio={report['conflict_ratio']})")
    print(f"  with features={report['matches_with_features']} rating={report['matches_with_rating']} "
          f"canary={report['matches_with_canary']} money_mode={report['matches_with_money_mode']}")
    if report["minimum_missing"]:
        print(f"  minimum_missing: {report['minimum_missing']}")
    print(f"  recommended next: {report['recommended_next_data_action']}")
    if report["excluded"]:
        print("  excluded:")
        for code, why in report["excluded"].items():
            print(f"    {code}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
