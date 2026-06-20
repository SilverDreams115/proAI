"""Compute internal team ratings from canonical results (R2 CLI).

Two modes:

    # READ-ONLY: compute, report, roll back. Never writes. Does NOT require
    # the team_rating_* tables to exist.
    python backend/scripts/compute_team_ratings.py --dry-run --draw-code PG-2338

    # WRITE (confirm-gated, mirrors the relink tooling double-confirmation):
    python backend/scripts/compute_team_ratings.py --apply \
        --confirm COMPUTE-TEAM-RATINGS-V1

The calculator is the pure R1 domain (``app.domain.team_rating``). The
mapping rules follow ``docs/team_rating_design.md``:

  * canonical, non-conflicting results only (``CanonicalResultRepository``);
  * conflicts / missing-score / sign-only are EXCLUDED by the calculator and
    reported in ``excluded_reasons``;
  * placeholder teams never receive a rating (pre-filtered out);
  * each match's namespace (club / national) is derived from its competition
    name, so both teams in a match always share a pool — there is no
    cross-namespace match to exclude.

SAFETY: ``--apply`` aborts unless ALL hold — exact ``--confirm`` token,
team_rating_* tables already exist, no active run with the same input
checksum, no incompatible active run. ``--apply`` is intentionally NOT run
during the R2 prep phase.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.db.session import SessionLocal
from app.db.session import managed_transaction
from app.domain.team_rating import ALGORITHM_VERSION
from app.domain.team_rating import TeamRatingCalculator
from app.domain.team_rating import TeamRatingInputMatch
from app.domain.team_rating import TeamRatingSnapshot
from app.domain.team_rating import default_config
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import TeamModel
from app.models.team_rating import team_rating_tables_exist
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.team_rating_repository import TeamRatingRepository

CONFIRM_TOKEN = "COMPUTE-TEAM-RATINGS-V1"

# National-team competition fingerprints (lower-cased substring match).
# Identical to the read-only coverage audit so namespaces stay consistent.
_NATIONAL_KEYWORDS = (
    "friendl", "amistos", "qualif", "world cup", "copa america", "copa américa",
    "euro", "nations league", "international", "concacaf", "gold cup", "afcon",
    "africa cup", "asian cup", "conmebol",
)


def namespace_for_competition(competition_name: str) -> str:
    name = (competition_name or "").lower()
    if any(kw in name for kw in _NATIONAL_KEYWORDS):
        return "national"
    return "club"


# --- canonical → input mapping (read-only) ----------------------------------


def build_input_matches(
    session: Session,
) -> tuple[list[TeamRatingInputMatch], dict[str, TeamModel], dict[str, int], int]:
    """Map canonical results to ``TeamRatingInputMatch`` rows.

    Returns ``(matches, teams_by_id, prefilter_excluded, results_considered)``
    where ``prefilter_excluded`` counts matches dropped BEFORE the calculator
    (e.g. placeholder teams) and ``results_considered`` is the number of
    matches that had at least one canonical result. Read-only.
    """
    all_match_ids = [
        row[0] for row in session.execute(select(MatchResultModel.match_id).distinct())
    ]
    repo = CanonicalResultRepository(session)
    canonical = repo.get_with_conflict_info(all_match_ids)

    match_rows = session.execute(
        select(MatchModel, CompetitionModel.name)
        .join(CompetitionModel, MatchModel.competition_id == CompetitionModel.id)
        .where(MatchModel.id.in_(list(canonical.keys())))
    ).all()
    match_by_id = {m.id: (m, comp_name) for m, comp_name in match_rows}

    # Pre-load team metadata for everything we might touch.
    team_ids: set[str] = set()
    for match, _comp in match_by_id.values():
        team_ids.add(match.home_team_id)
        team_ids.add(match.away_team_id)
    teams_by_id: dict[str, TeamModel] = {}
    if team_ids:
        for t in session.scalars(select(TeamModel).where(TeamModel.id.in_(list(team_ids)))):
            teams_by_id[t.id] = t

    matches: list[TeamRatingInputMatch] = []
    prefilter_excluded: dict[str, int] = {}
    results_considered = 0
    for match_id, canon in canonical.items():
        entry = match_by_id.get(match_id)
        if entry is None:
            continue
        results_considered += 1
        match, comp_name = entry
        home = teams_by_id.get(match.home_team_id)
        away = teams_by_id.get(match.away_team_id)
        # Placeholder teams must NEVER receive a rating (design §7).
        if (home is not None and home.is_placeholder) or (
            away is not None and away.is_placeholder
        ):
            prefilter_excluded["placeholder_team"] = (
                prefilter_excluded.get("placeholder_team", 0) + 1
            )
            continue
        result = canon.result
        matches.append(
            TeamRatingInputMatch(
                match_id=match_id,
                played_at=result.played_at,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                home_score=result.home_goals,
                away_score=result.away_goals,
                competition=comp_name,
                namespace=namespace_for_competition(comp_name),
                is_conflict=canon.is_conflicting,
                is_sign_only=False,  # match_results always carry numeric goals
            )
        )
    return matches, teams_by_id, prefilter_excluded, results_considered


def _snapshot_to_row(snap: TeamRatingSnapshot) -> dict[str, Any]:
    """One persistence-ready row dict (no run_id / id)."""
    return {
        "team_id": snap.team_id,
        "namespace": snap.namespace,
        "rating": snap.rating,
        "rating_delta": snap.rating_delta,
        "matches_count": snap.matches_count,
        "wins": snap.wins,
        "draws": snap.draws,
        "losses": snap.losses,
        "goals_for": snap.goals_for,
        "goals_against": snap.goals_against,
        "confidence_bucket": snap.confidence_bucket.value,
        "last_result_at": snap.last_result_at,
        "competitions_seen_json": json.dumps(snap.competitions_seen, sort_keys=True),
    }


# --- reporting --------------------------------------------------------------


def _team_name(teams_by_id: dict[str, TeamModel], team_id: str) -> str:
    t = teams_by_id.get(team_id)
    return t.name if t is not None else team_id


def _top_bottom(
    snapshots: dict[tuple[str, str], TeamRatingSnapshot],
    teams_by_id: dict[str, TeamModel],
    *,
    n: int = 10,
) -> dict[str, Any]:
    # Only rank teams with a confident-enough rating (medium+, ≥4 matches) so
    # the leaderboard is not dominated by 1500±ε no-history teams.
    ranked = sorted(
        (s for s in snapshots.values() if s.matches_count >= 4),
        key=lambda s: s.rating,
        reverse=True,
    )

    def _fmt(s: TeamRatingSnapshot) -> dict[str, Any]:
        return {
            "team": _team_name(teams_by_id, s.team_id),
            "namespace": s.namespace,
            "rating": round(s.rating, 1),
            "matches_count": s.matches_count,
            "confidence": s.confidence_bucket.value,
        }

    return {
        "ranked_team_count": len(ranked),
        "top": [_fmt(s) for s in ranked[:n]],
        "bottom": [_fmt(s) for s in ranked[-n:]],
    }


def pg2338_coverage(
    session: Session,
    snapshots: dict[tuple[str, str], TeamRatingSnapshot],
    teams_by_id: dict[str, TeamModel],
    draw_code: str,
) -> dict[str, Any]:
    repo = SlateRepository(session)
    found = repo.find_by_draw_code(draw_code)
    slate = repo.get_slate(found.id) if found is not None else None
    if slate is None:
        raise ValueError(f"draw_code {draw_code!r} not found")

    def _lookup(team_id: str) -> TeamRatingSnapshot | None:
        # A team can have both club and national snapshots; prefer whichever
        # has more matches (the pool the team actually lives in).
        candidates = [
            snapshots[(team_id, ns)]
            for ns in ("club", "national", "unknown")
            if (team_id, ns) in snapshots
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.matches_count)

    rows: list[dict[str, Any]] = []
    for link in sorted(slate.matches, key=lambda lnk: lnk.position):
        m = link.match
        th = _lookup(m.home_team_id)
        ta = _lookup(m.away_team_id)
        hc = th.matches_count if th else 0
        ac = ta.matches_count if ta else 0
        both = hc > 0 and ac > 0
        both_mp = hc >= 4 and ac >= 4
        rows.append({
            "position": link.position,
            "home": m.home_team.name,
            "away": m.away_team.name,
            "home_rating": round(th.rating, 1) if th else None,
            "away_rating": round(ta.rating, 1) if ta else None,
            "home_matches_count": hc,
            "away_matches_count": ac,
            "home_confidence": th.confidence_bucket.value if th else "no_rating",
            "away_confidence": ta.confidence_bucket.value if ta else "no_rating",
            "rating_diff": round(th.rating - ta.rating, 1) if (th and ta) else None,
            "both_have_rating": both,
            "both_medium_plus": both_mp,
        })
    summary = {
        "pg2338_matches": len(rows),
        "both_have_rating_count": sum(1 for r in rows if r["both_have_rating"]),
        "both_medium_plus_count": sum(1 for r in rows if r["both_medium_plus"]),
        "no_rating_positions": [
            r["position"]
            for r in rows
            if r["home_matches_count"] == 0 or r["away_matches_count"] == 0
        ],
    }
    return {"summary": summary, "rows": rows}


def build_report(
    session: Session, *, draw_code: str | None
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Compute ratings and assemble the report. Pure read; returns the
    report plus the serialized config and persistence-ready snapshot rows so
    ``--apply`` can reuse the exact same computation."""
    config = default_config()
    matches, teams_by_id, prefilter_excluded, results_considered = build_input_matches(
        session
    )
    calculator = TeamRatingCalculator(config)
    snapshots, summary = calculator.compute(matches)

    excluded_total = results_considered - summary.rated_match_count
    report: dict[str, Any] = {
        "algorithm_version": summary.algorithm_version,
        "config": asdict(config),
        "run_summary": {
            "source_result_count": results_considered,
            "rated_match_count": summary.rated_match_count,
            "excluded_match_count": excluded_total,
            "excluded_reasons": {
                **summary.excluded_reasons,
                **prefilter_excluded,
            },
            "team_snapshot_count": summary.team_count,
        },
        "checksums": {
            "config_checksum": summary.config_checksum,
            "input_checksum": summary.input_checksum,
            "output_checksum": summary.output_checksum,
        },
        "leaderboard": _top_bottom(snapshots, teams_by_id),
    }
    if draw_code:
        report["pg2338"] = pg2338_coverage(session, snapshots, teams_by_id, draw_code)

    snapshot_rows = [_snapshot_to_row(s) for s in snapshots.values()]
    config_json = json.dumps(asdict(config), sort_keys=True)
    return report, config_json, snapshot_rows


# --- apply (confirm-gated; not run during R2 prep) --------------------------


def run_apply(session: Session, *, confirm: str) -> dict[str, Any]:
    if confirm != CONFIRM_TOKEN:
        raise SystemExit(
            f"--apply requires --confirm {CONFIRM_TOKEN!r} (got {confirm!r}); aborting."
        )
    if not team_rating_tables_exist(db_session.engine):
        raise SystemExit(
            "team_rating_* tables do not exist; apply the migration draft first "
            "(see docs/team_rating_activation_protocol.md). Aborting."
        )

    report, config_json, snapshot_rows = build_report(session, draw_code=None)
    checksums = report["checksums"]
    rsum = report["run_summary"]
    repo = TeamRatingRepository(session)

    existing = repo.active_run_with_checksum(ALGORITHM_VERSION, checksums["input_checksum"])
    if existing is not None:
        raise SystemExit(
            f"active run {existing.id} already has input_checksum "
            f"{checksums['input_checksum']}; nothing to do. Aborting."
        )

    with managed_transaction(session):
        repo.supersede_previous_active(ALGORITHM_VERSION)
        run = repo.create_run(
            algorithm_version=ALGORITHM_VERSION,
            config_json=config_json,
            source_result_count=rsum["source_result_count"],
            rated_match_count=rsum["rated_match_count"],
            excluded_match_count=rsum["excluded_match_count"],
            input_checksum=checksums["input_checksum"],
            output_checksum=checksums["output_checksum"],
            status="computed",
        )
        repo.bulk_insert_snapshots(run.id, snapshot_rows)
        repo.mark_run_active(run.id)
    return {
        "applied": True,
        "run_id": run.id,
        "snapshots_written": len(snapshot_rows),
        **report["run_summary"],
        "checksums": checksums,
    }


# --- entrypoint -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute internal team ratings.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="compute + report, never write")
    mode.add_argument("--apply", action="store_true", help="persist a new active run (gated)")
    parser.add_argument("--confirm", default="", help=f"must equal {CONFIRM_TOKEN} for --apply")
    parser.add_argument("--draw-code", help="report rating coverage for this slate (dry-run)")
    args = parser.parse_args(argv)

    if args.apply:
        with SessionLocal() as session:
            result = run_apply(session, confirm=args.confirm)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0

    # dry-run: hard read-only — rollback no matter what.
    with SessionLocal() as session:
        try:
            report, _config_json, _rows = build_report(session, draw_code=args.draw_code)
            report["mode"] = "dry-run"
            report["wrote_anything"] = False
        finally:
            session.rollback()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
