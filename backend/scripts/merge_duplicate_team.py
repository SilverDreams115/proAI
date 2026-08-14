"""Merge a duplicate team row into its canonical twin (GUARDED).

Sibling of ``relink_slate_team``, for the case that tool refuses: the wrong
team on a slate position is not a placeholder but a second, thinner row for
the same real club. Both are ``is_placeholder=false``, so nothing flags them,
yet one carries the history and the other carries the fixtures — and a slate
linked to the empty one predicts against a vector of zeros.

Observed on PG-2345 #2: "Miami" (3 matches, 0 results) beside "Inter Miami"
(97 matches, 96 results). The slate pointed at "Miami" and the position came
out ``blocked`` with "local 0 partidos".

What it does, in one transaction:

* repoints every ``matches.home_team_id`` / ``away_team_id`` from the
  duplicate to the canonical team;
* moves the duplicate's aliases across, and records its name as a new alias
  so future ingestion resolves it to the canonical row;
* marks the duplicate inactive by renaming it ``<name> (merged)`` and
  flagging it a placeholder, so it can never be matched again;
* never deletes anything, and never touches match PKs — predictions,
  snapshots and results stay attached to their matches.

It refuses when the merge would collide: if both teams already appear in the
same fixture, repointing would violate ``uq_matches_fixture_identity``.

Usage::

    python -m scripts.merge_duplicate_team --duplicate "Miami" \\
        --canonical "Inter Miami" --dry-run

    python -m scripts.merge_duplicate_team --duplicate "Miami" \\
        --canonical "Inter Miami" --apply --confirm MERGE-DUPLICATE-TEAM
"""
from __future__ import annotations

import argparse

from sqlalchemy import or_, select

from app.db import session as db_session
from app.db.session import managed_transaction
from app.models.tables import MatchModel, MatchResultModel, TeamAliasModel, TeamModel
from app.services.normalization_service import NormalizationService

CONFIRM_TOKEN = "MERGE-DUPLICATE-TEAM"


def _stats(session, team: TeamModel) -> tuple[int, int]:
    matches = session.scalars(
        select(MatchModel).where(
            or_(MatchModel.home_team_id == team.id, MatchModel.away_team_id == team.id)
        )
    ).all()
    ids = [m.id for m in matches]
    results = 0
    if ids:
        results = len(
            session.scalars(
                select(MatchResultModel).where(MatchResultModel.match_id.in_(ids))
            ).all()
        )
    return len(matches), results


def _fixture_key(match: MatchModel, swap: dict[str, str]) -> tuple:
    home = swap.get(match.home_team_id, match.home_team_id)
    away = swap.get(match.away_team_id, match.away_team_id)
    return (match.competition_id, home, away, match.kickoff_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge a duplicate team into its canonical twin (guarded)."
    )
    parser.add_argument("--duplicate", required=True, help="exact name of the row to retire")
    parser.add_argument("--canonical", required=True, help="exact name of the row to keep")
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        dup = session.scalar(select(TeamModel).where(TeamModel.name == args.duplicate))
        canon = session.scalar(select(TeamModel).where(TeamModel.name == args.canonical))
        if dup is None:
            print(f"BLOCKED: duplicate team {args.duplicate!r} not found.")
            return 3
        if canon is None:
            print(f"BLOCKED: canonical team {args.canonical!r} not found.")
            return 3
        if dup.id == canon.id:
            print("BLOCKED: duplicate and canonical are the same row.")
            return 4

        dup_matches, dup_results = _stats(session, dup)
        canon_matches, canon_results = _stats(session, canon)
        print(f"== merge {dup.name!r} -> {canon.name!r} ==")
        print(f"  duplicate : {dup_matches} matches, {dup_results} results")
        print(f"  canonical : {canon_matches} matches, {canon_results} results")

        # Refusing this way round matters: merging the rich row into the thin
        # one would move the history onto the name nothing references.
        if dup_results > canon_results:
            print(
                f"BLOCKED: {dup.name!r} carries MORE results than {canon.name!r} "
                f"({dup_results} vs {canon_results}). Re-check which row is canonical; "
                "pass them the other way round if the merge direction is inverted."
            )
            return 4

        moving = session.scalars(
            select(MatchModel).where(
                or_(MatchModel.home_team_id == dup.id, MatchModel.away_team_id == dup.id)
            )
        ).all()
        swap = {dup.id: canon.id}
        existing = {
            _fixture_key(m, {}): m.id
            for m in session.scalars(select(MatchModel)).all()
            if m.id not in {x.id for x in moving}
        }
        collisions = []
        for match in moving:
            key = _fixture_key(match, swap)
            if key in existing:
                collisions.append((match.id, existing[key]))
            if swap.get(match.home_team_id, match.home_team_id) == swap.get(
                match.away_team_id, match.away_team_id
            ):
                collisions.append((match.id, "self-fixture"))
        if collisions:
            print(f"BLOCKED: {len(collisions)} fixture identity collision(s):")
            for a, b in collisions[:5]:
                print(f"    match {a} would collide with {b}")
            return 4

        print(f"  matches to repoint: {len(moving)}")
        aliases = session.scalars(
            select(TeamAliasModel).where(TeamAliasModel.team_id == dup.id)
        ).all()
        print(f"  aliases to carry over: {len(aliases)} (+1 for {dup.name!r} itself)")

        if not args.apply:
            print(f"\nDRY-RUN: nothing written. To apply: --apply --confirm {CONFIRM_TOKEN}")
            return 0
        if args.confirm != CONFIRM_TOKEN:
            print(f"\nBLOCKED: --apply requires --confirm {CONFIRM_TOKEN}. Nothing written.")
            return 2

        with managed_transaction(session):
            for match in moving:
                if match.home_team_id == dup.id:
                    match.home_team_id = canon.id
                if match.away_team_id == dup.id:
                    match.away_team_id = canon.id
                session.add(match)
            for alias in aliases:
                alias.team_id = canon.id
                session.add(alias)
            # Collected AFTER the move and from the moved rows too: the
            # duplicate usually owns an alias equal to its own name, and
            # reading only the canonical's pre-existing aliases would miss it
            # and then try to insert it again — `normalized_alias` is unique,
            # so that aborts the whole merge.
            session.flush()
            carried = session.scalars(
                select(TeamAliasModel).where(TeamAliasModel.team_id == canon.id)
            ).all()
            known = {a.normalized_alias for a in carried}
            known.update(a.normalized_alias for a in aliases)
            # `alias` carries its own unique index, so the raw spelling has to
            # be checked too — a row whose normalized form differs can still
            # collide on the name itself.
            known_raw = {a.alias for a in carried}
            known_raw.update(a.alias for a in aliases)
            # Must match how `find_team_by_alias` looks aliases up: the
            # resolver normalizes through NormalizationService (dash-joined,
            # stopwords stripped). Writing `name.lower()` here produced a
            # space-separated key that no lookup could ever match, so the
            # carried-over alias silently did nothing.
            normalized = NormalizationService().normalize_team_name(dup.name)
            if normalized not in known and dup.name not in known_raw:
                session.add(
                    TeamAliasModel(
                        team_id=canon.id, alias=dup.name, normalized_alias=normalized
                    )
                )
            # Renamed rather than deleted: the row stays for history, but the
            # name is freed and the placeholder flag keeps resolution away.
            dup.name = f"{dup.name} (merged)"
            dup.is_placeholder = True
            session.add(dup)

        print(f"\nMERGED: {len(moving)} matches repointed to {canon.name!r}; "
              f"{args.duplicate!r} retired and kept as an alias.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
