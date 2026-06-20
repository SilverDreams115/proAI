"""Read-only audit of persisted team-rating FEATURES (R3 validation).

Reads the latest ACTIVE ``elo_v1`` run and its snapshots and reports, per
slate position / per competition, what rating features a future feature layer
WOULD produce — without enabling the production flag and without writing
anything. It uses the pure builder from
``app/services/team_rating_feature_service.py`` so the audited features match
exactly what production would compute once activated.

Usage::

    python backend/scripts/audit_rating_features.py --draw-code PG-2338
    python backend/scripts/audit_rating_features.py --competition "International Friendlies"
    python backend/scripts/audit_rating_features.py --all-active-slates

SAFETY: hard read-only. Opens one session, never writes, rolls back at the
end. The production flag (PROAI_TEAM_RATING_FEATURE_ENABLED) is irrelevant
here — the auditor reads snapshots directly and uses the pure feature builder
so it can SHOW coverage even while the production path stays disabled.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.team_rating import ALGORITHM_VERSION
from app.models.tables import PredictionModel
from app.models.team_rating import TeamRatingSnapshotModel
from app.repositories.slate_repository import SlateRepository
from app.repositories.team_rating_repository import TeamRatingRepository
from app.services.team_rating_feature_service import RatingFeatures
from app.services.team_rating_feature_service import build_rating_features
from scripts.compute_team_ratings import namespace_for_competition

_MEDIUM_PLUS_MIN_MATCHES = 4
_MIN_MEDIUM_PLUS_RATE = 0.80
_PRIORITY = ("international friendlies", "copa libertadores", "brasileirao")


# --- snapshot index ---------------------------------------------------------


def load_active_run_snapshots(
    session: Session, algorithm_version: str = ALGORITHM_VERSION
) -> tuple[Any, dict[tuple[str, str], TeamRatingSnapshotModel]]:
    """Return (active_run, {(team_id, namespace): snapshot}) for the latest
    active run. Raises if there is no active run."""
    repo = TeamRatingRepository(session)
    run = repo.get_latest_active_run(algorithm_version)
    if run is None:
        raise SystemExit(f"no active run for algorithm_version {algorithm_version!r}")
    snaps = {
        (s.team_id, s.namespace): s for s in repo.get_snapshots_for_run(run.id)
    }
    return run, snaps


def _features_for_match(
    snaps: dict[tuple[str, str], TeamRatingSnapshotModel],
    home_team_id: str,
    away_team_id: str,
    competition_name: str,
) -> tuple[RatingFeatures, TeamRatingSnapshotModel | None, TeamRatingSnapshotModel | None, str]:
    namespace = namespace_for_competition(competition_name)
    home = snaps.get((home_team_id, namespace))
    away = snaps.get((away_team_id, namespace))
    feats = build_rating_features(home, away, namespace=namespace)
    return feats, home, away, namespace


def _status(home_present: bool, away_present: bool) -> str:
    if home_present and away_present:
        return "full_rating"
    if home_present or away_present:
        return "partial_rating"
    return "no_rating"


# --- prediction coverage (read-only) ----------------------------------------


def _latest_predictions_by_match(session: Session) -> dict[str, PredictionModel]:
    rows = session.scalars(
        select(PredictionModel).order_by(PredictionModel.generated_at.asc())
    )
    latest: dict[str, PredictionModel] = {}
    for pred in rows:
        latest[pred.match_id] = pred  # ascending → last wins = latest
    return latest


def _fallback_used(pred: PredictionModel | None) -> bool | None:
    if pred is None or not pred.sanity_audit_json:
        return None
    try:
        return bool(json.loads(pred.sanity_audit_json).get("fallback_used", False))
    except (ValueError, TypeError):
        return None


# --- PG-2338-style per-slate report -----------------------------------------


def audit_slate(
    session: Session,
    snaps: dict[tuple[str, str], TeamRatingSnapshotModel],
    draw_code: str,
) -> dict[str, Any]:
    repo = SlateRepository(session)
    found = repo.find_by_draw_code(draw_code)
    slate = repo.get_slate(found.id) if found is not None else None
    if slate is None:
        raise SystemExit(f"draw_code {draw_code!r} not found")

    rows: list[dict[str, Any]] = []
    for link in sorted(slate.matches, key=lambda lnk: lnk.position):
        m = link.match
        feats, home, away, namespace = _features_for_match(
            snaps, m.home_team_id, m.away_team_id, m.competition.name
        )
        hc = home.matches_count if home else 0
        ac = away.matches_count if away else 0
        status = _status(home is not None and hc > 0, away is not None and ac > 0)
        rows.append({
            "position": link.position,
            "home_team": m.home_team.name,
            "away_team": m.away_team.name,
            "home_rating": round(home.rating, 1) if home else None,
            "away_rating": round(away.rating, 1) if away else None,
            "rating_diff": feats.rating_diff if feats.rating_present else None,
            "home_confidence": home.confidence_bucket if home else "no_rating",
            "away_confidence": away.confidence_bucket if away else "no_rating",
            "both_rating_medium_plus": feats.both_rating_medium_plus,
            "rating_present": feats.rating_present,
            "rating_namespace": namespace,
            "home_matches_count": hc,
            "away_matches_count": ac,
            "status": status,
        })

    full = [r for r in rows if r["status"] == "full_rating"]
    partial = [r for r in rows if r["status"] == "partial_rating"]
    none_ = [r for r in rows if r["status"] == "no_rating"]
    both_mp = [r for r in rows if r["both_rating_medium_plus"]]
    edges = sorted(
        (r for r in rows if r["rating_diff"] is not None),
        key=lambda r: abs(r["rating_diff"]),
        reverse=True,
    )
    diffs = [abs(r["rating_diff"]) for r in rows if r["rating_diff"] is not None]

    def _edge(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "position": r["position"],
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "rating_diff": r["rating_diff"],
        }

    summary = {
        "draw_code": draw_code,
        "total_matches": len(rows),
        "full_rating_count": len(full),
        "partial_rating_count": len(partial),
        "no_rating_count": len(none_),
        "both_medium_plus_count": len(both_mp),
        "positions_missing_rating": [
            r["position"] for r in rows if r["status"] != "full_rating"
        ],
        "average_abs_rating_diff": round(sum(diffs) / len(diffs), 2) if diffs else None,
        "largest_rating_edges": [_edge(r) for r in edges[:3]],
        "smallest_rating_edges": [_edge(r) for r in edges[-3:]],
    }
    return {"summary": summary, "rows": rows}


# --- per-competition report over slate matches ------------------------------


def audit_competitions(
    session: Session,
    snaps: dict[tuple[str, str], TeamRatingSnapshotModel],
    *,
    competition: str | None,
    active_only: bool,
) -> list[dict[str, Any]]:
    repo = SlateRepository(session)
    latest_preds = _latest_predictions_by_match(session)

    @dataclass
    class _Agg:
        slate_match_count: int = 0
        rating_present: int = 0
        both_medium_plus: int = 0
        no_rating: int = 0
        partial_rating: int = 0
        fallback_yes: int = 0
        audit_total: int = 0

    aggs: dict[str, _Agg] = {}
    seen: set[tuple[str, int]] = set()  # de-dupe (slate_id, position)
    for slate in repo.list_slates():
        if active_only and getattr(slate, "is_archived", False):
            continue
        for link in slate.matches:
            m = link.match
            comp = m.competition.name
            if competition is not None and comp.lower() != competition.lower():
                continue
            key = (slate.id, link.position)
            if key in seen:
                continue
            seen.add(key)
            feats, home, away, _ns = _features_for_match(
                snaps, m.home_team_id, m.away_team_id, comp
            )
            agg = aggs.setdefault(comp, _Agg())
            agg.slate_match_count += 1
            hc = home.matches_count if home else 0
            ac = away.matches_count if away else 0
            status = _status(home is not None and hc > 0, away is not None and ac > 0)
            if feats.rating_present:
                agg.rating_present += 1
            if feats.both_rating_medium_plus:
                agg.both_medium_plus += 1
            if status == "no_rating":
                agg.no_rating += 1
            elif status == "partial_rating":
                agg.partial_rating += 1
            fb = _fallback_used(latest_preds.get(m.id))
            if fb is not None:
                agg.audit_total += 1
                if fb:
                    agg.fallback_yes += 1

    out: list[dict[str, Any]] = []
    for comp, agg in aggs.items():
        n = agg.slate_match_count
        mp_rate = round(agg.both_medium_plus / n, 3) if n else 0.0
        present_rate = round(agg.rating_present / n, 3) if n else 0.0
        fallback_rate = (
            round(agg.fallback_yes / agg.audit_total, 3) if agg.audit_total else None
        )
        usable_rate = (
            round(1.0 - agg.fallback_yes / agg.audit_total, 3)
            if agg.audit_total
            else None
        )
        blocker = ""
        if mp_rate < _MIN_MEDIUM_PLUS_RATE:
            blocker = f"thin_rating_coverage ({mp_rate} < {_MIN_MEDIUM_PLUS_RATE})"
        out.append({
            "competition": comp,
            "slate_match_count": n,
            "rating_present_rate": present_rate,
            "both_medium_plus_rate": mp_rate,
            "no_rating_count": agg.no_rating,
            "partial_rating_count": agg.partial_rating,
            "current_fallback_rate": fallback_rate,
            "current_usable_model_rate": usable_rate,
            "candidate_for_backtest": mp_rate >= _MIN_MEDIUM_PLUS_RATE,
            "blocker": blocker,
        })
    out.sort(
        key=lambda d: (
            not any(p in d["competition"].lower() for p in _PRIORITY),
            -d["both_medium_plus_rate"],
            -d["slate_match_count"],
        )
    )
    return out


# --- entrypoint -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only rating-feature coverage audit.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--draw-code", help="detailed per-position report for one slate")
    mode.add_argument("--competition", help="per-competition aggregate (this competition)")
    mode.add_argument(
        "--all-active-slates",
        action="store_true",
        help="per-competition aggregate over all non-archived slates",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        try:
            run, snaps = load_active_run_snapshots(session)
            run_info = {
                "run_id": run.id,
                "algorithm_version": run.algorithm_version,
                "status": run.status,
                "input_checksum": run.input_checksum,
                "output_checksum": run.output_checksum,
                "snapshot_count": len(snaps),
            }
            if args.draw_code:
                report = {"active_run": run_info, **audit_slate(session, snaps, args.draw_code)}
            elif args.competition:
                report = {
                    "active_run": run_info,
                    "competitions": audit_competitions(
                        session, snaps, competition=args.competition, active_only=False
                    ),
                }
            else:
                report = {
                    "active_run": run_info,
                    "competitions": audit_competitions(
                        session, snaps, competition=None, active_only=True
                    ),
                }
        finally:
            session.rollback()  # hard read-only guarantee

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
