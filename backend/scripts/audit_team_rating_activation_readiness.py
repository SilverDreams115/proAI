"""Read-only activation-readiness auditor for the team-rating gate (R5.6-A).

Prints whether the technical blockers before a minimal canary are cleared for a
slate / draw-code / competition scope: readiness checks, the canary plan, the
calibrator approval state and the rollback plan. It changes no production state
— no activation, no prediction regeneration, no feature/ticket snapshots, no DB
writes. The session is rolled back in all paths.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.repositories.slate_repository import SlateRepository
from app.services.team_rating_activation_readiness_service import (
    build_activation_readiness_payload,
)
from scripts.audit_team_rating_shadow import _enforce_read_only_transaction
from scripts.audit_team_rating_shadow import _links_for_scope


def _payload_for_args(session: Session, args: argparse.Namespace) -> dict[str, Any]:
    if args.slate_id is not None:
        repo = SlateRepository(session)
        slate = repo.get_slate(args.slate_id)
        if slate is None:
            raise SystemExit(f"slate_id {args.slate_id!r} not found")
        links = sorted(slate.matches, key=lambda link: link.position)
        return build_activation_readiness_payload(
            session, links, slate_id=slate.id, draw_code=getattr(slate, "draw_code", None)
        )

    links = _links_for_scope(
        session, draw_code=args.draw_code, competition=args.competition
    )
    if not links:
        raise SystemExit("no matches found for the requested scope")
    first_slate = links[0].slate
    return build_activation_readiness_payload(
        session,
        links,
        slate_id=getattr(first_slate, "id", "") if first_slate is not None else "",
        draw_code=args.draw_code
        or (getattr(first_slate, "draw_code", None) if first_slate is not None else None),
    )


def _print_human(report: dict[str, Any]) -> None:
    cal = report["calibrator"]
    dr = report["dry_run_summary"]
    plan = report["canary_plan"]
    print(f"scope: {report.get('draw_code') or report.get('slate_id')}")
    print(f"mode: {report['mode']} | production_active: {report['production_active']}")
    print(f"ready_for_canary: {report['ready_for_canary']}")
    print(f"ready_for_full_activation: {report['ready_for_full_activation']}")
    print(
        "calibrator: "
        f"{cal['id']} | approval_status={cal['approval_status']} | "
        f"approved_for_canary={cal['approved_for_canary']} | "
        f"productive_available={cal['productive_available']} | active={cal['active']}"
    )
    print("--- dry_run_summary ---")
    for key in (
        "total_matches",
        "would_route",
        "would_keep_current",
        "changed_top_pick_count",
        "max_probability_delta",
    ):
        print(f"  {key}: {dr[key]}")
    print("--- readiness_checks ---")
    for c in report["readiness_checks"]:
        suffix = f" (count={c['count']})" if c.get("count") is not None else ""
        print(f"  {c['check']}: {c['status']}{suffix}")
    print(f"canary_allowed_positions: {plan['canary_allowed_matches']}")
    print(f"blocked_positions: {plan['blocked_matches']}")
    print("--- rollback ---")
    for i, step in enumerate(plan["rollback"], start=1):
        print(f"  {i}. {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only activation-readiness audit for the team-rating gate."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--slate-id")
    mode.add_argument("--draw-code")
    mode.add_argument("--competition")
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
    import sys

    sys.exit(main())
