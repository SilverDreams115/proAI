"""Move one competition's matches off a conflated team row (GUARDED).

The third case in the family, after ``relink_slate_team`` (placeholder on a
slate position) and ``merge_duplicate_team`` (two rows, one club). Here it is
the opposite of a merge: ONE row holding TWO real clubs.

``NormalizationService.normalize_team_name`` strips ``club``, ``cd``, ``de``
and friends as stopwords, so homonyms across countries collapse onto the same
slug — ``Nacional``, ``Club Nacional`` and ``CD Nacional`` all normalize to
``nacional``. When two providers feed clubs that share a slug, both sets of
fixtures land on whichever row the resolver reaches first, and the team's form
becomes an average of two unrelated clubs.

Observed on ``Club Nacional``: 104 Portuguese matches (CD Nacional, Madeira)
sharing a row with 8 Copa Libertadores matches belonging to Club Nacional of
Asunción. PG-2346 #9 predicted the Portuguese fixture against that blend.

The split is by COMPETITION, because that is the axis the conflation follows —
each provider feeds one league, so the wrong matches always arrive under a
competition the club does not play in.

Safety contract:

* the target team must already exist, or be created with ``--create-target``;
* refuses if any moved fixture would collide with an existing identity;
* refuses to move every match off the source (that is a rename, not a split);
* match PKs never change, so results, predictions and feature snapshots stay
  attached to the fixtures they describe.

Usage::

    python -m scripts.split_team_entity --from-team "Club Nacional" \\
        --to-team "Club Nacional Asuncion" --competition "Copa Libertadores" \\
        --create-target --dry-run

    python -m scripts.split_team_entity --from-team "Club Nacional" \\
        --to-team "Club Nacional Asuncion" --competition "Copa Libertadores" \\
        --create-target --apply --confirm SPLIT-TEAM-ENTITY
"""
from __future__ import annotations

import argparse
import uuid

from sqlalchemy import or_, select

from app.db import session as db_session
from app.db.session import managed_transaction
from app.models.tables import (
    CompetitionModel,
    MatchModel,
    MatchResultModel,
    TeamAliasModel,
    TeamModel,
)
from app.services.normalization_service import NormalizationService

CONFIRM_TOKEN = "SPLIT-TEAM-ENTITY"


def _fixture_key(match: MatchModel, swap: dict[str, str]) -> tuple:
    home = swap.get(match.home_team_id, match.home_team_id)
    away = swap.get(match.away_team_id, match.away_team_id)
    return (match.competition_id, home, away, match.kickoff_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move one competition's matches to a separate team row (guarded)."
    )
    parser.add_argument("--from-team", required=True, help="exact name of the conflated row")
    parser.add_argument("--to-team", required=True, help="exact name of the row to move them to")
    parser.add_argument(
        "--competition",
        required=True,
        action="append",
        help="competition name whose matches move; repeat for several",
    )
    parser.add_argument("--create-target", action="store_true")
    parser.add_argument("--target-country", default=None)
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    normalizer = NormalizationService()

    with db_session.SessionLocal() as session:
        source = session.scalar(select(TeamModel).where(TeamModel.name == args.from_team))
        if source is None:
            print(f"BLOCKED: source team {args.from_team!r} not found.")
            return 3

        target = session.scalar(select(TeamModel).where(TeamModel.name == args.to_team))
        if target is None and not args.create_target:
            print(
                f"BLOCKED: target team {args.to_team!r} not found. "
                "Pass --create-target to create it."
            )
            return 3
        if target is not None and target.id == source.id:
            print("BLOCKED: source and target are the same row.")
            return 4

        target_slug = normalizer.normalize_team_name(args.to_team)
        source_slug = normalizer.normalize_team_name(source.name)
        if target_slug == source_slug:
            print(
                f"BLOCKED: {args.to_team!r} normalizes to {target_slug!r}, the same slug as "
                f"{source.name!r}. The split would not survive the next ingest — pick a name "
                "that survives stopword stripping (add the city, not another 'Club'/'CD')."
            )
            return 4

        competitions = session.scalars(
            select(CompetitionModel).where(CompetitionModel.name.in_(args.competition))
        ).all()
        missing = set(args.competition) - {c.name for c in competitions}
        if missing:
            print(f"BLOCKED: competition(s) not found: {sorted(missing)}")
            return 3
        competition_ids = {c.id for c in competitions}

        all_matches = session.scalars(
            select(MatchModel).where(
                or_(MatchModel.home_team_id == source.id, MatchModel.away_team_id == source.id)
            )
        ).all()
        moving = [m for m in all_matches if m.competition_id in competition_ids]
        staying = [m for m in all_matches if m.competition_id not in competition_ids]

        print(f"== split {source.name!r} -> {args.to_team!r} ==")
        print(f"  competitions moving : {', '.join(args.competition)}")
        print(f"  matches moving      : {len(moving)}")
        print(f"  matches staying     : {len(staying)}")

        if not moving:
            print("BLOCKED: no matches match that competition filter.")
            return 4
        if not staying:
            print(
                "BLOCKED: the filter selects every match on the row. That is a rename, "
                "not a split — use it deliberately instead of this tool."
            )
            return 4

        target_id = target.id if target is not None else str(uuid.uuid4())
        swap = {source.id: target_id}
        moving_ids = {m.id for m in moving}
        existing = {
            _fixture_key(m, {}): m.id
            for m in session.scalars(select(MatchModel))
            if m.id not in moving_ids
        }
        collisions = [
            (m.id, existing[_fixture_key(m, swap)])
            for m in moving
            if _fixture_key(m, swap) in existing
        ]
        if collisions:
            print(f"BLOCKED: {len(collisions)} fixture identity collision(s):")
            for a, b in collisions[:5]:
                print(f"    match {a} would collide with {b}")
            return 4

        results_moving = len(
            session.scalars(
                select(MatchResultModel).where(MatchResultModel.match_id.in_(list(moving_ids)))
            ).all()
        )
        print(f"  results carried over: {results_moving}")
        print(f"  target slug         : {target_slug} (source keeps {source_slug!r})")

        if not args.apply:
            print(f"\nDRY-RUN: nothing written. To apply: --apply --confirm {CONFIRM_TOKEN}")
            return 0
        if args.confirm != CONFIRM_TOKEN:
            print(f"\nBLOCKED: --apply requires --confirm {CONFIRM_TOKEN}. Nothing written.")
            return 2

        with managed_transaction(session):
            if target is None:
                target = TeamModel(
                    id=target_id,
                    name=args.to_team,
                    country=args.target_country,
                    is_placeholder=False,
                )
                session.add(target)
                session.flush()
                # The alias is what makes the split durable: without it the
                # next ingest of this club resolves by slug again and walks
                # straight back onto the conflated row.
                session.add(
                    TeamAliasModel(
                        team_id=target.id,
                        alias=args.to_team,
                        normalized_alias=target_slug,
                    )
                )
            for match in moving:
                if match.home_team_id == source.id:
                    match.home_team_id = target.id
                if match.away_team_id == source.id:
                    match.away_team_id = target.id
                session.add(match)

        print(
            f"APPLIED: moved {len(moving)} match(es) ({results_moving} with results) "
            f"from {source.name!r} to {target.name!r}."
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
