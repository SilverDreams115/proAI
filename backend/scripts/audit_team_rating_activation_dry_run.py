"""Read-only controlled-activation dry-run auditor for the team-rating gate (R5.5).

Prints what enabling the controlled gate would do for a slate / draw-code /
competition scope: routing, simulated pick changes, probability deltas and the
blockers that prevent real activation. It changes no production state — no
prediction regeneration, no feature/ticket snapshots, no DB writes. The session
is rolled back in all paths.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import session as db_session
from app.repositories.slate_repository import SlateRepository
from app.services.team_rating_activation_dry_run_service import (
    build_activation_dry_run_payload,
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
        return build_activation_dry_run_payload(
            session,
            links,
            slate_id=slate.id,
            draw_code=getattr(slate, "draw_code", None),
        )

    links = _links_for_scope(
        session, draw_code=args.draw_code, competition=args.competition
    )
    if not links:
        raise SystemExit("no matches found for the requested scope")
    first_slate = links[0].slate
    return build_activation_dry_run_payload(
        session,
        links,
        slate_id=getattr(first_slate, "id", "") if first_slate is not None else "",
        draw_code=args.draw_code
        or (getattr(first_slate, "draw_code", None) if first_slate is not None else None),
    )


def _print_human(report: dict[str, Any]) -> None:
    s = report["summary"]
    print(f"scope: {report.get('draw_code') or report.get('slate_id')}")
    print(f"mode: {report['mode']} | production_active: {report['production_active']}")
    print(f"safe_to_activate: {report['safe_to_activate']}")
    print(f"dry_run_probability_model: {report['dry_run_probability_model']}")
    print("--- summary ---")
    for key in (
        "total_matches",
        "eligible_if_enabled",
        "would_route",
        "would_keep_current",
        "blocked_by_rating",
        "blocked_by_review",
        "blocked_by_hard_sanity",
        "changed_top_pick_count",
        "changed_confidence_bucket_count",
        "max_probability_delta",
        "positions_would_route",
        "positions_changed_pick",
    ):
        print(f"  {key}: {s[key]}")
    print(f"activation_blockers: {report['activation_blockers']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only controlled-activation dry-run for the team-rating gate."
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
