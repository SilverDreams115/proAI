"""R6.4 / R7.0 — Validate completed-slate results (read-only by default).

Compares predictions vs results for a completed slate (PG-2337, PGM-800, …)
from three sources:

  1. local ``match_results`` already in the DB,
  2. the read-only results provider dry-run,
  3. a manually curated official results file (``--manual-file``).

By default it writes nothing. Applying a manual official file requires BOTH
``--apply`` and the exact ``--confirm`` token, and even then only if every guard
rule passes (complete/in-range/unique positions, sign↔score agreement, no
conflict with existing local results, high-confidence source).

Usage::

    python -m scripts.validate_completed_slate_results --draw-code PG-2337
    python -m scripts.validate_completed_slate_results --draw-code PGM-800
    python -m scripts.validate_completed_slate_results --all-completed
    python -m scripts.validate_completed_slate_results --manual-file results.json --dry-run
    python -m scripts.validate_completed_slate_results --manual-file results.json \\
        --apply --confirm APPLY-COMPLETED-SLATE-RESULTS
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import managed_transaction, read_only_transaction
from app.repositories.slate_repository import SlateRepository
from app.services.completed_slate_manual_results import (
    ManualResultsError,
    apply_manual_results,
    evaluate_manual_apply,
    load_manual_results_file,
)
from app.services.completed_slate_results_validation_service import (
    build_completed_slate_validation_for_draw_code,
    build_completed_slates_validation,
)

CONFIRM_TOKEN = "APPLY-COMPLETED-SLATE-RESULTS"


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


def _print_manual_eval(report: dict[str, Any]) -> None:
    print(f"== manual results · {report['draw_code']} ==")
    print(f"  source       : {report['source']} (confidence={report['source_confidence']})")
    print(f"  checksum     : {report['checksum']}")
    print(f"  provided     : {report['provided_count']}/{report['match_count']}  "
          f"coverage={int(report['coverage'] * 100)}%  conflictos={report['conflicts']}")
    print(f"  ready_to_apply: {report['ready_to_apply']}")
    print(f"  blockers     : {report['blockers'] or 'ninguno'}")
    for row in report["rows"]:
        print(f"    pos{row['position']:>2} sign={row['sign'] or '-'} score={row['score'] or '-'} "
              f"local={row['existing_local_sign'] or '-'} "
              f"conflict={row['conflicts_existing']}")


def _run_manual(args: argparse.Namespace) -> int:
    try:
        manual = load_manual_results_file(args.manual_file)
    except ManualResultsError as exc:
        print(f"INVALID MANUAL FILE: {exc}")
        return 6

    do_apply = args.apply and args.confirm == CONFIRM_TOKEN

    if not do_apply:
        # Dry-run (default). Read-only evaluation.
        with db_session.SessionLocal() as session:
            with read_only_transaction(session):
                slate = SlateRepository(session).find_by_draw_code(manual.draw_code)
                if slate is None:
                    print(f"BLOCKED: slate {manual.draw_code} not found.")
                    return 3
                report = evaluate_manual_apply(session, slate, manual)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            _print_manual_eval(report)
            if args.apply and args.confirm != CONFIRM_TOKEN:
                print(f"NOTE: apply requested but confirm token wrong; nothing written. "
                      f"Use --confirm {CONFIRM_TOKEN}.")
        return 0

    # Apply path (guarded): writes only if ready_to_apply.
    with db_session.SessionLocal() as session:
        with managed_transaction(session):
            slate = SlateRepository(session).find_by_draw_code(manual.draw_code)
            if slate is None:
                print(f"BLOCKED: slate {manual.draw_code} not found. No results written.")
                return 3
            outcome = apply_manual_results(session, slate, manual)
    if not outcome["applied"]:
        print(f"NOT APPLIED: {outcome['reason']} blockers={outcome.get('blockers')}. "
              "No results written.")
        return 4
    print(
        f"APPLIED: {outcome['draw_code']} · inserted {outcome['inserted']} results "
        f"into match_results (positions {outcome['inserted_positions']}) "
        f"from {outcome['source']} (confidence={outcome['source_confidence']}, "
        f"checksum={outcome['checksum']})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate completed-slate results (read-only by default).")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--draw-code")
    scope.add_argument("--all-completed", action="store_true")
    scope.add_argument("--manual-file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="(default for --manual-file)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    if args.manual_file:
        return _run_manual(args)

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
