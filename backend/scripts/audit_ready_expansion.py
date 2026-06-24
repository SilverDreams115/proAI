"""R6.3 — Readiness expansion auditor (read-only, changes no state).

Explains, per slate fixture, why it is not READY and what real datum would
unblock it. ``safe_to_promote_now`` is true only when evidence is already
sufficient, so the audit never invents a READY. Writes nothing.

Usage::

    python -m scripts.audit_ready_expansion --draw-code PG-2338
    python -m scripts.audit_ready_expansion --draw-code PGM-801
    python -m scripts.audit_ready_expansion --active-upcoming
    python -m scripts.audit_ready_expansion --active-upcoming --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.repositories.slate_repository import SlateRepository
from app.services.readiness_expansion_service import (
    build_active_slates_ready_expansion,
    build_ready_expansion,
)


def _print_slate(report: dict[str, Any]) -> None:
    s = report["slate"]
    print(f"== {s['draw_code']} ({s['week_type']}, {s['match_count']} partidos) ==")
    print(f"  READY ahora                 : {report['ready_now']}")
    print(f"  READY potencial (ext. data) : {report['ready_potential_with_external_data']}")
    print(f"  READY potencial (provider)  : {report['ready_potential_after_provider_results']}")
    print(f"  promociones seguras         : {report['safe_promotions'] or 'ninguna'}")
    print(f"  motivo no-promote           : {report['no_promote_reason']}")
    for m in report["matches"]:
        print(
            f"    pos{m['position']:>2} {m['match'][:30]:30} {m['current_status']:9} "
            f"blocked_by={m['blocked_by']} mejora_con={m['can_be_improved_by']} "
            f"safe={m['safe_to_promote_now']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Readiness expansion audit (R6.3, read-only).")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--draw-code")
    scope.add_argument("--active-upcoming", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            if args.active_upcoming:
                report = build_active_slates_ready_expansion(session)
            else:
                slate = SlateRepository(session).find_by_draw_code(args.draw_code)
                if slate is None:
                    raise SystemExit("slate not found for the requested draw code")
                report = build_ready_expansion(session, slate)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif args.active_upcoming:
        print(f"active/upcoming: {report['slate_count']} slates · "
              f"promociones seguras totales: {report['total_safe_promotions']}")
        print(report["no_promote_reason"])
        for slate_report in report["slates"]:
            _print_slate(slate_report)
    else:
        _print_slate(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
