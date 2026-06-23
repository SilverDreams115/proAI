"""R5.7 — Read-only ticket/optimizer canary dry-run auditor.

Compares, in memory, the current ticket against the ticket the optimizer would
produce from the canary effective probabilities — for a draw-code, a slate-id,
or every active/upcoming slate. It activates nothing, integrates no optimizer
and writes no row (no snapshots, no predictions). The session is rolled back in
all paths and set READ ONLY on PostgreSQL.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.repositories.slate_repository import SlateRepository
from app.services.ticket_canary_dry_run_service import (
    build_active_slates_ticket_canary_dry_run,
    build_ticket_canary_dry_run,
)


def _enforce_read_only_transaction(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


def _payload_for_args(session: Session, args: argparse.Namespace) -> dict[str, Any]:
    if args.active_upcoming:
        return build_active_slates_ticket_canary_dry_run(session)
    repo = SlateRepository(session)
    if args.slate_id is not None:
        slate = repo.get_slate(args.slate_id)
    else:
        slate = repo.find_by_draw_code(args.draw_code)
    if slate is None:
        raise SystemExit("slate not found for the requested scope")
    return build_ticket_canary_dry_run(session, slate)


def _print_slate(report: dict[str, Any]) -> None:
    slate = report["slate"]
    s = report["summary"]
    cur = s["current_ticket"]
    can = s["canary_ticket"]
    print(f"== {slate['draw_code']} ({slate['week_type']}, {slate['match_count']} partidos) ==")
    print(f"  current : simple={cur['simple_count']} double={cur['double_count']} triple={cur['triple_count']}")
    print(f"  canary  : simple={can['simple_count']} double={can['double_count']} triple={can['triple_count']}")
    print(f"  changed_positions     : {s['changed_positions']}")
    print(f"  simple_removed        : {s['simple_removed_positions']}")
    print(f"  new_doubles           : {s['new_double_positions']}")
    print(f"  new_triples           : {s['new_triple_positions']}")
    print(f"  canary_active_positions: {s['canary_active_positions']}")
    print(f"  risk_delta            : {s['risk_delta']}  ticket_changed={s['ticket_changed']}")
    ws = report["write_safety"]
    print(f"  write_safety          : writes={ws['writes_performed']} snapshot={ws['snapshot_created']}")


def _print_human(report: dict[str, Any]) -> None:
    if report.get("mode") == "ticket_canary_dry_run_active_upcoming":
        print(f"active/upcoming slates: {report['slate_count']}")
        for slate_report in report["slates"]:
            _print_slate(slate_report)
    else:
        _print_slate(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only ticket canary dry-run audit (R5.7)."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--slate-id")
    mode.add_argument("--draw-code")
    mode.add_argument("--active-upcoming", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        try:
            _enforce_read_only_transaction(session)
            report = _payload_for_args(session, args)
        finally:
            session.rollback()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
