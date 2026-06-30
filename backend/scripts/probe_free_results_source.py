"""R6.3 — Probe a free results source (read-only, no writes).

Validates whether a free results provider (default football-data.org) is
configured and usable, and — with a draw-code/active-upcoming — how well it
covers the slate fixtures. It writes nothing and never fails fatally on a
missing key or insufficient coverage: those are reported as statuses.

Usage::

    python -m scripts.probe_free_results_source --provider football_data_org
    python -m scripts.probe_free_results_source --provider football_data_org --draw-code PG-2338
    python -m scripts.probe_free_results_source --provider football_data_org --active-upcoming --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.repositories.slate_repository import SlateRepository
from app.services.active_slate_scope import build_active_slate_scope
from app.services.results_provider_service import (
    build_slate_results_dry_run,
    probe_provider,
)
from app.services.slate_service import SlateService


def _build(session, args: argparse.Namespace) -> dict[str, Any]:
    probe = probe_provider(args.provider)
    out: dict[str, Any] = {"probe": probe}
    if args.active_upcoming:
        slate_service = SlateService(SlateRepository(session))
        slates = []
        for info in build_active_slate_scope(session):
            slate = slate_service.get_slate(info.slate_id)
            if slate is not None:
                slates.append(build_slate_results_dry_run(slate, provider=args.provider))
        out["slates"] = slates
    elif args.draw_code:
        slate = SlateRepository(session).find_by_draw_code(args.draw_code)
        if slate is None:
            raise SystemExit("slate not found for the requested draw code")
        out["slates"] = [build_slate_results_dry_run(slate, provider=args.provider)]
    return out


def _print_human(report: dict[str, Any]) -> None:
    p = report["probe"]
    print(f"== probe: {p['provider']} ==")
    print(f"  status        : {p.get('status')}")
    print(f"  api_key_present: {p['api_key_present']}")
    print(f"  enabled       : {p['enabled']} · dry_run_only={p['dry_run_only']}")
    if "matches_found" in p:
        print(f"  matches_found : {p['matches_found']} (finished={p.get('finished_found')})")
        print(f"  competitions  : {', '.join(p.get('competitions', [])) or 'ninguna'}")
    if p.get("note"):
        print(f"  note          : {p['note']}")
    for slate in report.get("slates", []):
        cov = slate["coverage"]
        s = slate["slate"]
        print(f"  -- {s['draw_code']} ({s['match_count']} partidos) status={slate['status']} "
              f"coverage={cov['matched']}/{cov['total']} ({int(cov['rate'] * 100)}%)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe a free results source (R6.3, read-only).")
    parser.add_argument("--provider", default="football_data_org")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--draw-code")
    scope.add_argument("--active-upcoming", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = _build(session, args)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
