"""Delete matches that no slate points at any more (GUARDED).

A slate position is re-pointed, not edited: when the fixture identity of a
position changes — a placeholder team resolved to its canonical row, a
competition corrected, a name fixed in the local context — ``refresh-current``
creates a NEW match for the new identity and links the position to it. The old
match stays behind with its predictions attached and nothing referencing it.

Those leftovers are not merely untidy. They keep occupying their
``uq_matches_fixture_identity`` slot, which is what makes ``merge_duplicate_team``
and ``relink_slate_team`` refuse a legitimate fix: the merge would recreate an
identity an orphan already holds. Cleaning them is what unblocks the guarded
tools.

Safety contract — it only ever deletes a match when ALL of these hold:

* no ``progol_slate_matches`` row references it (never linked, or unlinked);
* it carries no ``match_results`` — anything with a real result is history,
  regardless of who references it;
* it carries no ``match_feature_snapshots`` — those are the vectors the
  learning dataset replays, so a match that has one is evidence;
* its kickoff is inside ``--within-days`` of now (default 30), so old
  historical rows can never be swept up by a broad invocation.

Predictions attached to a deleted match go with it: a prediction against a
fixture no slate serves cannot be scored and is not part of any learning row
(``_build_all_rows`` reads persisted jornada scores, which are slate-scoped).

Usage::

    python -m scripts.prune_orphan_slate_matches --dry-run
    python -m scripts.prune_orphan_slate_matches --match-id 5f50d10c-... --dry-run
    python -m scripts.prune_orphan_slate_matches --apply --confirm PRUNE-ORPHAN-MATCHES
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session as db_session
from app.db.session import managed_transaction
from app.models.tables import (
    EvidenceItemModel,
    MatchFeatureSnapshotModel,
    MatchLiveResultModel,
    MatchModel,
    MatchResultModel,
    MatchStatSnapshotModel,
    PlayerAvailabilityModel,
    PredictionModel,
    ProgolSlateMatchModel,
    SourceDocumentModel,
    TeamModel,
)

CONFIRM_TOKEN = "PRUNE-ORPHAN-MATCHES"

# Every table whose `match_id` is NOT NULL: deleting the match without
# clearing these first makes SQLAlchemy try to NULL the column and the
# delete dies on the constraint. `match_results` and
# `match_feature_snapshots` are absent on purpose — carrying either one
# is a blocker above, so a deletable match never has them.
DEPENDENT_MODELS = (
    PredictionModel,
    EvidenceItemModel,
    MatchLiveResultModel,
    MatchStatSnapshotModel,
    PlayerAvailabilityModel,
)


def _candidates(session, within_days: int, match_id: str | None) -> list[dict]:
    linked = {row[0] for row in session.execute(select(ProgolSlateMatchModel.match_id))}
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)

    statement = select(MatchModel)
    if match_id:
        statement = statement.where(MatchModel.id == match_id)

    out: list[dict] = []
    for match in session.scalars(statement):
        kickoff = match.kickoff_at
        if kickoff is not None and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        blockers: list[str] = []
        if match.id in linked:
            blockers.append("linked_to_slate")
        if session.scalar(
            select(MatchResultModel).where(MatchResultModel.match_id == match.id)
        ):
            blockers.append("has_result")
        if session.scalar(
            select(MatchFeatureSnapshotModel).where(
                MatchFeatureSnapshotModel.match_id == match.id
            )
        ):
            blockers.append("has_feature_snapshot")
        if kickoff is None or kickoff < cutoff:
            blockers.append("outside_window")

        dependents: dict[str, list] = {}
        for model in DEPENDENT_MODELS:
            dependents[model.__name__] = session.scalars(
                select(model).where(model.match_id == match.id)
            ).all()

        # A row nothing points at and that never carried a prediction is just
        # ingested history; sweeping those would delete the league corpus.
        if not dependents["PredictionModel"] and not match_id:
            continue

        home = session.get(TeamModel, match.home_team_id)
        away = session.get(TeamModel, match.away_team_id)
        out.append(
            {
                "match": match,
                "label": f"{getattr(home, 'name', '?')} vs {getattr(away, 'name', '?')}",
                "kickoff": kickoff,
                "dependents": dependents,
                "blockers": blockers,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete orphaned slate matches and their predictions (guarded)."
    )
    parser.add_argument("--match-id", default=None, help="restrict to a single match id")
    parser.add_argument("--within-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        rows = _candidates(session, args.within_days, args.match_id)
        deletable = [r for r in rows if not r["blockers"]]
        skipped = [r for r in rows if r["blockers"]]

        print(f"== orphan slate matches (window={args.within_days}d) ==")
        for row in deletable:
            attached = ", ".join(
                f"{name.replace('Model', '')}={len(items)}"
                for name, items in row["dependents"].items()
                if items
            )
            print(
                f"  DELETE {row['match'].id[:8]} {row['label'][:44]:44} "
                f"kickoff={str(row['kickoff'])[:10]} {attached}"
            )
        for row in skipped:
            print(
                f"  KEEP   {row['match'].id[:8]} {row['label'][:44]:44} "
                f"blockers={','.join(row['blockers'])}"
            )
        print(f"  deletable: {len(deletable)}  kept: {len(skipped)}")

        if not args.apply:
            print(
                "DRY-RUN: no write performed. To apply: "
                f"--apply --confirm {CONFIRM_TOKEN}"
            )
            return 0
        if args.confirm != CONFIRM_TOKEN:
            print(f"BLOCKED: --apply requires --confirm {CONFIRM_TOKEN}.")
            return 2
        if not deletable:
            print("Nothing to delete.")
            return 0

        with managed_transaction(session):
            removed: dict[str, int] = {}
            unlinked_documents = 0
            for row in deletable:
                for name, items in row["dependents"].items():
                    for item in items:
                        # source_documents.linked_evidence_id points back at
                        # the evidence row and both columns are nullable, so
                        # the document is detached rather than deleted: the
                        # captured source stays auditable and
                        # `prune-source-documents` decides its fate on its own
                        # retention schedule.
                        if isinstance(item, EvidenceItemModel):
                            for document in session.scalars(
                                select(SourceDocumentModel).where(
                                    SourceDocumentModel.linked_evidence_id == item.id
                                )
                            ):
                                document.linked_evidence_id = None
                                document.matched_match_id = None
                                unlinked_documents += 1
                        session.delete(item)
                    if items:
                        removed[name] = removed.get(name, 0) + len(items)
                session.flush()
                session.delete(row["match"])
            if unlinked_documents:
                print(f"  unlinked {unlinked_documents} source document(s)")

        detail = ", ".join(
            f"{count} {name.replace('Model', '')}" for name, count in sorted(removed.items())
        )
        print(
            f"APPLIED: deleted {len(deletable)} orphan match(es)"
            + (f" and {detail}." if detail else ".")
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
