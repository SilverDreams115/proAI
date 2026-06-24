"""R6.4 — Slate options auditor (read-only).

Always prints the ticket options for a slate (aggressive/balanced/conservative/
manual) with their pricing, respecting the Money Mode decision: when NO JUGAR
nothing is recommended and the action is "no comprar boleto". Writes nothing.

Usage::

    python -m scripts.audit_slate_options --draw-code PG-2338
    python -m scripts.audit_slate_options --draw-code PGM-801
    python -m scripts.audit_slate_options --active-upcoming
    python -m scripts.audit_slate_options --active-upcoming --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.slate_options_service import (
    build_active_slates_options,
    build_slate_options_for_draw_code,
)


def _print_slate(report: dict[str, Any]) -> None:
    print(f"== {report['draw_code']} ({report['week_type']}) ==")
    print(f"  Money Mode: {report['money_mode_decision']} · acción: {report['recommended_action']}")
    print(f"  pricing: {report['pricing_note']}")
    for opt in report["options"]:
        cost = opt["estimated_cost"]
        cost_txt = f"${cost} MXN" if cost is not None else "precio no verificado"
        star = " *RECOMENDADA*" if opt["recommended"] else ""
        print(f"    {opt['name']:24}{star}")
        print(f"       playable={opt['playable']} riesgo={opt['risk_level']} "
              f"dobles={opt['double_count']} triples={opt['triple_count']} "
              f"combinaciones={opt['combinations']} costo={cost_txt}")
        print(f"       {opt['reason']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slate options audit (R6.4, read-only).")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--draw-code")
    scope.add_argument("--active-upcoming", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            if args.active_upcoming:
                report = build_active_slates_options(session)
            else:
                report = build_slate_options_for_draw_code(session, args.draw_code)
                if report is None:
                    raise SystemExit("slate not found for the requested draw code")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif args.active_upcoming:
        print(f"active/upcoming: {report['slate_count']} slates")
        for slate_report in report["slates"]:
            _print_slate(slate_report)
    else:
        _print_slate(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
