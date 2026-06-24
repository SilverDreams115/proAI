"""R6.4 — Validate completed-slate results (read-only dry-run).

Compares predictions vs local/provider results for a completed slate
(PG-2337, PGM-800, …) to decide whether tracking/learning can be activated. It
writes nothing; applying results is a separate, confirmed step.

Usage::

    python -m scripts.validate_completed_slate_results --draw-code PG-2337
    python -m scripts.validate_completed_slate_results --draw-code PGM-800
    python -m scripts.validate_completed_slate_results --all-completed
    python -m scripts.validate_completed_slate_results --draw-code PG-2337 --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.completed_slate_results_validation_service import (
    build_completed_slate_validation_for_draw_code,
    build_completed_slates_validation,
)


def _print_slate(report: dict[str, Any]) -> None:
    print(f"== {report['draw_code']} ({report['week_type']}, {report['match_count']} partidos) ==")
    print(f"  predicciones : {report['predictions_count']}/{report['match_count']}")
    print(f"  resultados locales   : {report['local_results_count']}/{report['match_count']}")
    print(f"  resultados proveedor : {report['provider_results_count']}/{report['match_count']} "
          f"(provider status={report['provider_status']})")
    print(f"  coverage     : {int(report['coverage'] * 100)}%  conflictos={report['conflicts']}")
    print(f"  aciertos     : {report['hits']}/{report['match_count']}")
    print(f"  ready_to_apply: {report['ready_to_apply']}")
    print(f"  blockers     : {report['blockers'] or 'ninguno'}")
    for m in report["matches"]:
        print(f"    pos{m['position']:>2} {m['match'][:30]:30} pred={m['prediction'] or '-'} "
              f"local={m['local_result'] or '-'} provider={m['provider_result'] or '-'} "
              f"status={m['status']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate completed-slate results (R6.4, read-only).")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--draw-code")
    scope.add_argument("--all-completed", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            if args.all_completed:
                report = build_completed_slates_validation(session)
            else:
                report = build_completed_slate_validation_for_draw_code(session, args.draw_code)
                if report is None:
                    raise SystemExit("slate not found for the requested draw code")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif args.all_completed:
        print(f"completed slates: {report['slate_count']} · ready={report['ready_count']}")
        for slate_report in report["slates"]:
            _print_slate(slate_report)
    else:
        _print_slate(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
