"""Safe relink of PG-2338 placeholder teams to their canonical entities.

This script ONLY ever relinks the placeholder *team_id* of a slate's match
to a canonical, non-placeholder team. By design it does NOT:

* change ``composition_hash`` (it is payload-derived; relinking team_ids
  does not alter the raw payload names it is computed from);
* bump ``slate_version``;
* touch ``predictions`` or ``ticket_recommendation_snapshots``;
* delete any historical row (placeholder teams are left in place);
* touch pos13 (República Del Congo — ambiguous Congo vs DR Congo).

The relink is performed as an IN-PLACE update of ``matches.home_team_id`` /
``matches.away_team_id``. Because predictions/snapshots are keyed on the
match PK (``match_id``), and the PK is preserved, they stay correctly
attached. An in-place update is only possible when no canonical match with
the target identity already exists (otherwise the unique constraint
``uq_matches_fixture_identity`` would be violated and the relink would have
to repoint the slate link to a different ``match_id``, orphaning
predictions — which this script refuses to do).

Usage::

    # report only (read-only):
    python backend/scripts/relink_pg2338_mapping.py \
        --slate-id 30146702-399d-40de-afff-e376b1c01396 --dry-run

    # mutate (requires BOTH flags; never runs by accident):
    python backend/scripts/relink_pg2338_mapping.py \
        --slate-id <id> --apply --confirm RELINK-PG-2338
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TicketRecommendationSnapshotModel
from scripts.diagnose_pg2338_mapping import _find_candidates, _unique_candidate, _alias_key, _CONGO_KEYS

# Hard preconditions — the script refuses to run against anything else.
EXPECTED_DRAW_CODE = "PG-2338"
EXPECTED_COMPOSITION_HASH = (
    "308aafc934654c488841835c1a8548225ad4f6cb7d4d8b065b8fa1efe873cc6e"
)
# Only these positions may ever be applied; pos13 is intentionally excluded.
APPLICABLE_POSITIONS = frozenset({2, 3, 8})
CONFIRM_TOKEN = "RELINK-PG-2338"


class RelinkAbort(RuntimeError):
    """Raised when a hard safety precondition fails. Never written through."""


@dataclass(frozen=True)
class RelinkRow:
    position: int
    current_home_team_id: str
    current_away_team_id: str
    current_home_name: str
    current_away_name: str
    proposed_home_team_id: str
    proposed_away_team_id: str
    proposed_home_name: str | None
    proposed_away_name: str | None
    would_change_team_id: bool
    would_change_match_id: bool
    would_touch_predictions: bool
    would_touch_snapshots: bool
    composition_hash_before: str
    composition_hash_after: str
    safe_to_apply: bool
    status: str
    reason: str
    current_match_id: str
    attached_prediction_count: int
    attached_snapshot_count: int


def _load_slate(session: Session, slate_id: str) -> ProgolSlateModel:
    slate = session.get(ProgolSlateModel, slate_id)
    if slate is None:
        raise RelinkAbort(f"slate {slate_id!r} not found")
    if slate.draw_code != EXPECTED_DRAW_CODE:
        raise RelinkAbort(
            f"refusing to run: slate draw_code is {slate.draw_code!r}, "
            f"expected {EXPECTED_DRAW_CODE!r}"
        )
    if slate.composition_hash != EXPECTED_COMPOSITION_HASH:
        raise RelinkAbort(
            "refusing to run: composition_hash drift — "
            f"stored={slate.composition_hash!r}, expected={EXPECTED_COMPOSITION_HASH!r}"
        )
    return slate


def _replacement_match_id(
    session: Session,
    *,
    match: MatchModel,
    home_team_id: str,
    away_team_id: str,
) -> str | None:
    return session.scalar(
        select(MatchModel.id).where(
            MatchModel.competition_id == match.competition_id,
            MatchModel.home_team_id == home_team_id,
            MatchModel.away_team_id == away_team_id,
            MatchModel.kickoff_at == match.kickoff_at,
            MatchModel.id != match.id,
        )
    )


def _prediction_count(session: Session, match_id: str) -> int:
    # Predictions ARE keyed by match_id (the match PK).
    return int(
        session.scalar(
            select(func.count())
            .select_from(PredictionModel)
            .where(PredictionModel.match_id == match_id)
        )
        or 0
    )


def _slate_snapshot_count(session: Session, slate_id: str) -> int:
    # Snapshots are keyed by (slate_id, composition_hash) — one ticket per
    # slate-version, NOT per match. There is no match_id column.
    return int(
        session.scalar(
            select(func.count())
            .select_from(TicketRecommendationSnapshotModel)
            .where(
                TicketRecommendationSnapshotModel.slate_id == slate_id,
                TicketRecommendationSnapshotModel.is_valid.is_(True),
            )
        )
        or 0
    )


def _slate_link_count(session: Session, match_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ProgolSlateMatchModel)
            .where(ProgolSlateMatchModel.match_id == match_id)
        )
        or 0
    )


def _evaluate_row(
    session: Session, slate: ProgolSlateModel, link: ProgolSlateMatchModel, comp_hash: str
) -> RelinkRow:
    match = link.match
    home = match.home_team
    away = match.away_team

    # pos13 (and any Congo-named placeholder) is hard-blocked for review.
    is_congo = _alias_key(home.name) in _CONGO_KEYS or _alias_key(away.name) in _CONGO_KEYS

    home_cands = _find_candidates(session, home.name) if home.is_placeholder else []
    away_cands = _find_candidates(session, away.name) if away.is_placeholder else []
    home_pick = _unique_candidate(home_cands) if home.is_placeholder else None
    away_pick = _unique_candidate(away_cands) if away.is_placeholder else None

    proposed_home_id = home_pick.team_id if (home.is_placeholder and home_pick) else home.id
    proposed_away_id = away_pick.team_id if (away.is_placeholder and away_pick) else away.id
    proposed_home_name = home_pick.name if (home.is_placeholder and home_pick) else None
    proposed_away_name = away_pick.name if (away.is_placeholder and away_pick) else None

    would_change_team_id = proposed_home_id != home.id or proposed_away_id != away.id

    replacement_id = None
    if would_change_team_id:
        replacement_id = _replacement_match_id(
            session, match=match, home_team_id=proposed_home_id, away_team_id=proposed_away_id
        )
    would_change_match_id = replacement_id is not None

    pred_count = _prediction_count(session, match.id)
    snap_count = _slate_snapshot_count(session, slate.id)
    shared = _slate_link_count(session, match.id) > 1

    ambiguous = (
        (home.is_placeholder and home_cands and home_pick is None)
        or (away.is_placeholder and away_cands and away_pick is None)
    )
    unresolved = (
        (home.is_placeholder and not home_cands) or (away.is_placeholder and not away_cands)
    )

    # Default: nothing to do.
    status = "no_change"
    reason = "no placeholder side needs relink"
    safe = False

    if is_congo:
        status, reason = "needs_review_mapping", (
            "Congo vs DR Congo ambiguity; excluded until a local source disambiguates"
        )
    elif ambiguous:
        status, reason = "needs_review_mapping", "placeholder has multiple canonical candidates"
    elif unresolved:
        status, reason = "provider_missing", "placeholder has no canonical candidate"
    elif would_change_team_id and link.position not in APPLICABLE_POSITIONS:
        status, reason = "blocked_not_applicable", (
            f"position {link.position} is not in the approved set {sorted(APPLICABLE_POSITIONS)}"
        )
    elif would_change_team_id and shared:
        status, reason = "blocked_shared_match", "match is linked by more than one slate"
    elif would_change_team_id and would_change_match_id:
        status, reason = "blocked_match_id_change", (
            "a canonical match already exists; in-place relink impossible — would orphan "
            f"{pred_count} prediction(s)/{snap_count} snapshot(s); not handled here"
        )
    elif would_change_team_id:
        # In-place team_id update: PK preserved, hash/version/predictions untouched.
        status, reason = "safe_to_apply", (
            "unique canonical candidate; in-place team_id update keeps match_id, "
            "composition_hash, slate_version and predictions/snapshots unchanged"
        )
        safe = True

    return RelinkRow(
        position=link.position,
        current_home_team_id=home.id,
        current_away_team_id=away.id,
        current_home_name=home.name,
        current_away_name=away.name,
        proposed_home_team_id=proposed_home_id,
        proposed_away_team_id=proposed_away_id,
        proposed_home_name=proposed_home_name,
        proposed_away_name=proposed_away_name,
        would_change_team_id=would_change_team_id,
        would_change_match_id=would_change_match_id,
        # A safe in-place relink never touches predictions/snapshots; only a
        # match_id change (which we refuse) would.
        would_touch_predictions=would_change_match_id and pred_count > 0,
        would_touch_snapshots=would_change_match_id and snap_count > 0,
        composition_hash_before=comp_hash,
        composition_hash_after=comp_hash,  # invariant: hash never changes
        safe_to_apply=safe,
        status=status,
        reason=reason,
        current_match_id=match.id,
        attached_prediction_count=pred_count,
        attached_snapshot_count=snap_count,
    )


def build_relink_plan(session: Session, slate_id: str) -> dict:
    slate = _load_slate(session, slate_id)
    comp_hash = slate.composition_hash or ""
    rows = [
        _evaluate_row(session, slate, link, comp_hash)
        for link in sorted(slate.matches, key=lambda link: link.position)
    ]
    return {
        "slate_id": slate.id,
        "draw_code": slate.draw_code,
        "slate_version": slate.slate_version,
        "composition_hash_before": comp_hash,
        "composition_hash_after": comp_hash,
        "rows": [asdict(row) for row in rows],
        "safe_positions": [r.position for r in rows if r.safe_to_apply],
        "review_positions": [r.position for r in rows if r.status == "needs_review_mapping"],
        "would_change_composition_hash": False,
        "would_change_slate_version": False,
    }


def apply_relink(session: Session, slate_id: str, *, confirm: str) -> dict:
    """Apply the in-place team_id relink for the approved safe positions.

    Hard-guarded: requires the exact confirmation token, aborts on any
    composition_hash drift, and never changes hash/version or touches
    predictions/snapshots. Callers must pass ``confirm=CONFIRM_TOKEN``.
    """
    if confirm != CONFIRM_TOKEN:
        raise RelinkAbort(
            "apply requires the exact confirmation token "
            f"{CONFIRM_TOKEN!r}; got {confirm!r}"
        )
    slate = _load_slate(session, slate_id)
    hash_before = slate.composition_hash
    version_before = slate.slate_version
    plan = build_relink_plan(session, slate_id)
    applied: list[int] = []
    links = {link.position: link for link in slate.matches}
    for row in plan["rows"]:
        if not row["safe_to_apply"]:
            continue
        if row["position"] not in APPLICABLE_POSITIONS:
            raise RelinkAbort(f"refusing to apply non-approved position {row['position']}")
        link = links[row["position"]]
        match = link.match
        if match.home_team.is_placeholder and row["proposed_home_team_id"] != match.home_team_id:
            match.home_team_id = row["proposed_home_team_id"]
        if match.away_team.is_placeholder and row["proposed_away_team_id"] != match.away_team_id:
            match.away_team_id = row["proposed_away_team_id"]
        session.add(match)
        applied.append(row["position"])
    # Invariants must hold: hash and version untouched.
    if slate.composition_hash != hash_before or slate.slate_version != version_before:
        raise RelinkAbort("invariant violated: composition_hash/slate_version changed")
    session.flush()
    return {"applied_positions": applied, "composition_hash": hash_before, "slate_version": version_before}


def main() -> None:
    parser = argparse.ArgumentParser(description="PG-2338 placeholder relink (safe, in-place).")
    parser.add_argument("--slate-id", required=True)
    parser.add_argument("--dry-run", action="store_true", help="report only; never writes")
    parser.add_argument("--apply", action="store_true", help="mutate; requires --confirm")
    parser.add_argument("--confirm", default="", help=f"must equal {CONFIRM_TOKEN!r} to apply")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive")
    if not args.apply and not args.dry_run:
        raise SystemExit("pass --dry-run (report) or --apply --confirm <token> (mutate)")

    with SessionLocal() as session:
        if args.apply:
            result = apply_relink(session, args.slate_id, confirm=args.confirm)
            session.commit()
            print(json.dumps({"mode": "apply", **result}, indent=2, sort_keys=True))
        else:
            plan = build_relink_plan(session, args.slate_id)
            # Hard read-only guarantee: roll back anything SQLAlchemy may have
            # autoflushed during reads (there should be nothing).
            session.rollback()
            print(json.dumps({"mode": "dry-run", **plan}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
