"""Safe, generic relink of one slate position's placeholder team to a canonical team.

Successor of the PG-2338-specific relink tooling: same safety contract, but
parameterized so any future bracket-slot placeholder ("Ganador E.U.A.",
"Ganador SF1", …) can be resolved through one guarded pipeline step instead
of a per-slate script.

By design it ONLY performs an IN-PLACE update of ``matches.home_team_id`` /
``matches.away_team_id`` for the single match linked at the given position:

* the match PK is preserved, so predictions / snapshots / results stay attached;
* ``composition_hash`` and ``slate_version`` are not touched;
* the CURRENT team on that side must be a placeholder (``is_placeholder=true``);
* the TARGET team must already exist and not be a placeholder;
* it refuses if the resulting fixture identity would collide with an existing
  match (``uq_matches_fixture_identity``);
* nothing is deleted; the placeholder team row stays for history.

Usage::

    # read-only report:
    python -m scripts.relink_slate_team --draw-code PGM-803 --position 4 \
        --side away --target-team "Bélgica" --dry-run

    # mutate (requires BOTH flags):
    python -m scripts.relink_slate_team --draw-code PGM-803 --position 4 \
        --side away --target-team "Bélgica" --apply --confirm RELINK-SLATE-TEAM
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db import session as db_session
from app.models.tables import (
    MatchModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    TeamModel,
)

CONFIRM_TOKEN = "RELINK-SLATE-TEAM"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Relink one slate position's placeholder team to a canonical team (guarded)."
    )
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--position", type=int, required=True)
    parser.add_argument("--side", choices=("home", "away"), required=True)
    parser.add_argument("--target-team", required=True, help="exact name of the canonical team")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        slate = session.scalar(
            select(ProgolSlateModel).where(ProgolSlateModel.draw_code == args.draw_code)
        )
        if slate is None:
            print(f"BLOCKED: slate {args.draw_code!r} not found.")
            return 3
        link = session.scalar(
            select(ProgolSlateMatchModel).where(
                ProgolSlateMatchModel.slate_id == slate.id,
                ProgolSlateMatchModel.position == args.position,
            )
        )
        if link is None:
            print(f"BLOCKED: position {args.position} not found in {args.draw_code}.")
            return 3
        match = session.get(MatchModel, link.match_id)
        current_team_id = match.home_team_id if args.side == "home" else match.away_team_id
        current = session.get(TeamModel, current_team_id)
        target = session.scalar(select(TeamModel).where(TeamModel.name == args.target_team))

        if target is None:
            print(f"BLOCKED: target team {args.target_team!r} not found (it must already exist).")
            return 3
        if not current.is_placeholder:
            print(
                f"BLOCKED: current {args.side} team {current.name!r} is not a placeholder; "
                "this tool only replaces placeholder slots."
            )
            return 4
        if target.is_placeholder:
            print(f"BLOCKED: target team {target.name!r} is itself a placeholder.")
            return 4
        if target.id == current_team_id:
            print("BLOCKED: target equals current team; nothing to do.")
            return 4

        new_home = target.id if args.side == "home" else match.home_team_id
        new_away = target.id if args.side == "away" else match.away_team_id
        collision = session.scalar(
            select(MatchModel).where(
                MatchModel.competition_id == match.competition_id,
                MatchModel.home_team_id == new_home,
                MatchModel.away_team_id == new_away,
                MatchModel.kickoff_at == match.kickoff_at,
                MatchModel.id != match.id,
            )
        )
        if collision is not None:
            print(
                f"BLOCKED: fixture identity collision with existing match {collision.id}; "
                "an in-place relink would violate uq_matches_fixture_identity. "
                "Refusing (repointing match_id would orphan predictions)."
            )
            return 5

        predictions = session.scalars(
            select(PredictionModel).where(PredictionModel.match_id == match.id)
        ).all()
        print(f"== relink {args.draw_code} pos {args.position} ({args.side}) ==")
        print(f"  match        : {match.id}")
        print(f"  current team : {current.name!r} (placeholder={current.is_placeholder})")
        print(f"  target team  : {target.name!r} (id={target.id})")
        print(f"  predictions preserved (match PK unchanged): {len(predictions)}")

        if not args.apply or args.confirm != CONFIRM_TOKEN:
            print(f"DRY-RUN: no write performed. To apply: --apply --confirm {CONFIRM_TOKEN}")
            return 0

        if args.side == "home":
            match.home_team_id = target.id
        else:
            match.away_team_id = target.id
        session.commit()
        print(f"APPLIED: {args.draw_code} pos {args.position} {args.side} team -> {target.name!r}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
