"""R7.0 — Completed-slate learning inventory CLI (read-only).

Classifies every slate by its learning state (comparable / pending / conflict /
…) so the post-jornada loop knows which slates can be learned from. Writes
nothing.

Usage::

    python -m scripts.learning_inventory --all
    python -m scripts.learning_inventory --json
    python -m scripts.learning_inventory --draw-code PG-2337
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.completed_slate_inventory_service import (
    build_completed_slate_inventory,
    build_slate_inventory_for_draw_code,
)


def _print_item(it: dict[str, Any]) -> None:
    flag = "✓" if it["comparable"] else "·"
    print(
        f" {flag} {it['draw_code']:>9} [{it['state']}] "
        f"{it['week_type']}, {it['match_count']} partidos"
    )
    print(
        f"      pred={it['prediction_count']}/{it['match_count']} "
        f"local={it['local_result_count']} provider={it['provider_result_count']} "
        f"canonical={it['canonical_result_count']} conflicts={it['conflicts']} "
        f"coverage={int(it['coverage'] * 100)}%"
    )
    print(
        f"      lineage={it['classification']} comparable={it['comparable']} "
        f"blockers={it['blockers'] or 'ninguno'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Completed-slate learning inventory (R7.0, read-only)."
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="(default) every slate")
    scope.add_argument("--draw-code")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            if args.draw_code:
                report: Any = build_slate_inventory_for_draw_code(session, args.draw_code)
                if report is None:
                    raise SystemExit("slate not found for the requested draw code")
            else:
                report = build_completed_slate_inventory(session)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    if args.draw_code:
        _print_item(report)
        return 0

    print(
        f"learning inventory: {report['slate_count']} slates · "
        f"comparable={report['comparable_count']}"
    )
    print(f"by_state: {report['by_state']}")
    for item in report["slates"]:
        _print_item(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
