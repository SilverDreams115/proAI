"""Re-run the competition inference over a slate's placeholder fixtures (GUARDED).

A Progol position that no feed ever reported is promoted against a placeholder
match, and that match still needs a competition: `ProgolFixtureResolver.
infer_competition_for_pair` guesses one from team history. The guess is kept
for scoring — it carries the blend weights and the competition profile — even
though `_cap_inferred_competition_policy` withdraws its live-pick permission.

So a wrong guess is not cosmetic. PGM-809 shipped with Fenerbahce vs Lyon as
"F1", Nijmegen vs Bodo/Glimt as "N1" and Independiente Rivadavia vs Fluminense
as "Brasileirao": three cross-border ties scored with a domestic league's
model, inherited from whichever of the two clubs happened to be known.

This script re-asks the (fixed) resolver and re-points the match rows whose
answer changed. When the resolver now declines to guess, the fixture lands on
the slate's synthetic `Progol Concurso <draw_code>` competition, which claims
no benchmark at all.

It only ever touches `matches.competition_id`, and only on rows that are
`is_placeholder = true` and linked to the named slate. Predictions are NOT
regenerated here — refresh them afterwards so the new competition actually
reaches the model:

    curl -XPOST /api/predictions/slates/<slate_id>/refresh

Usage::

    python -m scripts.repoint_inferred_competitions --draw-code PGM-809 --dry-run
    python -m scripts.repoint_inferred_competitions --draw-code PGM-809 \\
        --apply --confirm REPOINT-INFERRED-COMPETITIONS
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db import session as db_session
from app.db.session import managed_transaction
from app.models.tables import (
    CompetitionModel,
    MatchModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
)
from app.services.progol_fixture_resolver import ProgolFixtureResolver

CONFIRM_TOKEN = "REPOINT-INFERRED-COMPETITIONS"


def _placeholder_competition_name(draw_code: str) -> str:
    """Same name `slate_proposal_service` uses for an unguessable fixture."""
    return f"Progol Concurso {draw_code}"


def _plan(session, draw_code: str) -> tuple[ProgolSlateModel | None, list[dict]]:
    slate = session.scalar(
        select(ProgolSlateModel).where(ProgolSlateModel.draw_code == draw_code)
    )
    if slate is None:
        return None, []

    resolver = ProgolFixtureResolver(session)
    rows: list[dict] = []
    links = session.scalars(
        select(ProgolSlateMatchModel)
        .where(ProgolSlateMatchModel.slate_id == slate.id)
        .order_by(ProgolSlateMatchModel.position)
    ).all()

    for link in links:
        match = session.get(MatchModel, link.match_id)
        if match is None:
            continue
        current = session.get(CompetitionModel, match.competition_id)
        current_name = getattr(current, "name", "?")
        home = match.home_team.name
        away = match.away_team.name

        if not bool(match.is_placeholder):
            rows.append(
                {
                    "position": link.position,
                    "label": f"{home} vs {away}",
                    "current": current_name,
                    "proposed": current_name,
                    "action": "KEEP",
                    "why": "resolved fixture — the competition was observed, not guessed",
                    "match": match,
                }
            )
            continue

        inferred = resolver.infer_competition_for_pair(home, away)
        proposed = (
            inferred.name if inferred is not None else _placeholder_competition_name(draw_code)
        )
        rows.append(
            {
                "position": link.position,
                "label": f"{home} vs {away}",
                "current": current_name,
                "proposed": proposed,
                "action": "KEEP" if proposed == current_name else "REPOINT",
                "why": (
                    "inference unchanged"
                    if proposed == current_name
                    else (
                        "no competition both clubs play — no guess is defensible"
                        if inferred is None
                        else "the competition both clubs actually play"
                    )
                ),
                "match": match,
                "inferred": inferred,
            }
        )
    return slate, rows


def _competition_for(session, row: dict, draw_code: str) -> CompetitionModel:
    inferred = row.get("inferred")
    if inferred is not None:
        return inferred
    name = _placeholder_competition_name(draw_code)
    existing = session.scalar(select(CompetitionModel).where(CompetitionModel.name == name))
    if existing is not None:
        return existing
    created = CompetitionModel(name=name, country=None, season=None)
    session.add(created)
    session.flush()
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-point a slate's placeholder fixtures onto the competition "
        "the current inference returns (guarded)."
    )
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        slate, rows = _plan(session, args.draw_code)
        if slate is None:
            print(f"BLOCKED: no slate with draw_code={args.draw_code}.")
            return 2

        repoints = [row for row in rows if row["action"] == "REPOINT"]
        print(f"== inferred competitions for {args.draw_code} (slate {slate.id}) ==")
        for row in rows:
            arrow = (
                f"{row['current']} -> {row['proposed']}"
                if row["action"] == "REPOINT"
                else row["current"]
            )
            print(
                f"  {row['action']:8} {row['position']:>2} {row['label'][:42]:42} "
                f"{arrow}"
            )
            if row["action"] == "REPOINT":
                print(f"           {row['why']}")
        print(f"  repoint: {len(repoints)}  keep: {len(rows) - len(repoints)}")

        if not args.apply:
            print(f"DRY-RUN: no write performed. To apply: --apply --confirm {CONFIRM_TOKEN}")
            return 0
        if args.confirm != CONFIRM_TOKEN:
            print(f"BLOCKED: --apply requires --confirm {CONFIRM_TOKEN}.")
            return 2
        if not repoints:
            print("Nothing to re-point.")
            return 0

        with managed_transaction(session):
            for row in repoints:
                competition = _competition_for(session, row, args.draw_code)
                row["match"].competition_id = competition.id
            session.flush()
        print(f"APPLIED: {len(repoints)} fixture(s) re-pointed.")
        print(
            "Predictions still carry the old competition — refresh the slate "
            f"(POST /api/predictions/slates/{slate.id}/refresh) before reading Money Mode."
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
