"""Read-only shadow audit for the inactive team-rating gate (R5.1).

The script answers what would happen if the controlled International
Friendlies team-rating gate were enabled, without changing production state:
no prediction regeneration, no feature snapshots, no ticket snapshots, no
calibration artifacts, and no DB writes. The session is rolled back in all
paths.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import SessionLocal
from app.domain.team_rating_gate_config import CRITICAL_SANITY_BLOCKERS
from app.domain.team_rating_gate_config import GATE_CALIBRATOR_METADATA
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.repositories.slate_repository import SlateRepository
from app.services.team_rating_feature_service import build_rating_features
from app.services.team_rating_shadow_service import TeamRatingShadowDecision
from app.services.team_rating_shadow_service import evaluate_team_rating_shadow_for_match
from app.services.team_rating_shadow_service import has_rating_blocker
from scripts.audit_rating_features import load_active_run_snapshots
from scripts.compute_team_ratings import namespace_for_competition


def _latest_predictions_by_match(session: Session) -> dict[str, PredictionModel]:
    rows = session.scalars(select(PredictionModel).order_by(PredictionModel.generated_at.asc()))
    latest: dict[str, PredictionModel] = {}
    for pred in rows:
        latest[pred.match_id] = pred
    return latest


def _legacy_sanity_flags(pred: PredictionModel | None) -> list[str]:
    if pred is None or not pred.sanity_audit_json:
        return []
    try:
        audit = json.loads(pred.sanity_audit_json)
    except (TypeError, ValueError):
        return []
    flags = {str(flag).strip().upper() for flag in audit.get("sanity_flags", [])}
    if bool(audit.get("fallback_used", False)):
        flags.add("FALLBACK_USED")
    if str(audit.get("evidence_level", "")).lower() == "low":
        flags.add("LOW_EVIDENCE")
    final_status = str(audit.get("final_status", "")).upper()
    if final_status in {"BLOCKED", "REVISAR"}:
        flags.add(final_status)
    return [flag for flag in sorted(flags) if flag in CRITICAL_SANITY_BLOCKERS]


def _rating_facts(
    snaps: dict[tuple[str, str], Any],
    match: MatchModel,
) -> dict[str, Any]:
    namespace = namespace_for_competition(match.competition.name)
    home = snaps.get((match.home_team_id, namespace))
    away = snaps.get((match.away_team_id, namespace))
    feats = build_rating_features(home, away, namespace=namespace)
    home_matches = home.matches_count if home is not None else 0
    away_matches = away.matches_count if away is not None else 0
    return {
        "namespace": namespace,
        "rating_present": feats.rating_present,
        "both_rating_medium_plus": feats.both_rating_medium_plus,
        "home_rating_confidence": home.confidence_bucket if home is not None else "no_rating",
        "away_rating_confidence": away.confidence_bucket if away is not None else "no_rating",
        "home_matches_count": home_matches,
        "away_matches_count": away_matches,
        "home_rating": round(float(home.rating), 6) if home is not None else None,
        "away_rating": round(float(away.rating), 6) if away is not None else None,
        "rating_diff": feats.rating_diff if feats.rating_present else None,
    }


def _status(row: dict[str, Any]) -> str:
    if row["rating_present"]:
        return "full_rating"
    if row["home_matches_count"] > 0 or row["away_matches_count"] > 0:
        return "partial_rating"
    return "no_rating"


def _links_for_scope(
    session: Session, *, draw_code: str | None, competition: str | None
) -> list[ProgolSlateMatchModel]:
    repo = SlateRepository(session)
    if draw_code is not None:
        found = repo.find_by_draw_code(draw_code)
        slate = repo.get_slate(found.id) if found is not None else None
        if slate is None:
            raise SystemExit(f"draw_code {draw_code!r} not found")
        return sorted(slate.matches, key=lambda link: link.position)

    assert competition is not None
    links: list[ProgolSlateMatchModel] = []
    seen: set[tuple[str, int]] = set()
    for slate in repo.list_slates():
        for link in slate.matches:
            if link.match.competition.name.strip().lower() != competition.strip().lower():
                continue
            key = (slate.id, link.position)
            if key in seen:
                continue
            seen.add(key)
            links.append(link)
    return sorted(
        links,
        key=lambda link: (
            link.slate.draw_code if link.slate is not None else "",
            link.position,
        ),
    )


def _row_for_link(
    link: ProgolSlateMatchModel,
    *,
    snaps: dict[tuple[str, str], Any],
    latest_predictions: dict[str, PredictionModel],
    assume_gate_enabled: bool,
    assume_calibrator_available: bool,
) -> dict[str, Any]:
    match = link.match
    facts = _rating_facts(snaps, match)
    sanity_flags = _legacy_sanity_flags(latest_predictions.get(match.id))
    decision = evaluate_team_rating_shadow_for_match(
        competition_name=match.competition.name,
        rating_present=facts["rating_present"],
        both_rating_medium_plus=facts["both_rating_medium_plus"],
        home_rating_confidence=facts["home_rating_confidence"],
        away_rating_confidence=facts["away_rating_confidence"],
        rating_diff=facts["rating_diff"],
        sanity_flags=sanity_flags,
        assume_gate_enabled=assume_gate_enabled,
        assume_calibrator_available=assume_calibrator_available,
    )
    return {
        "slate_draw_code": link.slate.draw_code if link.slate is not None else None,
        "position": link.position,
        "match_id": match.id,
        "home_team": match.home_team.name,
        "away_team": match.away_team.name,
        "competition": match.competition.name,
        "rating_status": _status(facts),
        "legacy_sanity_flags": sanity_flags,
        **facts,
        **asdict(decision),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [
        TeamRatingShadowDecision(
            shadow_enabled=row["shadow_enabled"],
            gate_enabled=row["gate_enabled"],
            eligible_current=row["eligible_current"],
            eligible_if_enabled=row["eligible_if_enabled"],
            would_use_rating_model=row["would_use_rating_model"],
            would_remain_fallback=row["would_remain_fallback"],
            blockers=list(row["blockers"]),
            rating_diff=row["rating_diff"],
            both_medium_plus=row["both_medium_plus"],
            calibrator_required=row["calibrator_required"],
            calibrator_available=row["calibrator_available"],
        )
        for row in rows
    ]

    def _positions(pred) -> list[int]:
        return [int(row["position"]) for row in rows if pred(row)]

    blocked_by_rating = sum(1 for decision in decisions if has_rating_blocker(decision))
    blocked_by_calibrator = sum(
        1
        for decision in decisions
        if "calibrator_unavailable" in decision.blockers
        and "competition_not_allowed" not in decision.blockers
        and not has_rating_blocker(decision)
    )
    return {
        "total_matches": len(rows),
        "eligible_current": sum(1 for row in rows if row["eligible_current"]),
        "eligible_if_enabled": sum(1 for row in rows if row["eligible_if_enabled"]),
        "would_use_rating_model_current": sum(
            1 for row in rows if row["gate_enabled"] and row["eligible_current"]
        ),
        "would_use_rating_model_if_enabled": sum(
            1 for row in rows if row["would_use_rating_model"]
        ),
        "would_remain_fallback": sum(1 for row in rows if row["would_remain_fallback"]),
        "blocked_by_flag": sum(
            1 for row in rows if not row["gate_enabled"] and not row["eligible_current"]
        ),
        "blocked_by_competition": sum(
            1 for row in rows if "competition_not_allowed" in row["blockers"]
        ),
        "blocked_by_rating": blocked_by_rating,
        "blocked_by_calibrator": blocked_by_calibrator,
        "blocked_by_sanity": sum(
            1
            for row in rows
            if row["eligible_if_enabled"]
            and not row["would_use_rating_model"]
            and "sanity_blocked" in row["blockers"]
        ),
        "positions_eligible_if_enabled": _positions(lambda row: row["eligible_if_enabled"]),
        "positions_blocked": _positions(lambda row: not row["would_use_rating_model"]),
    }


def audit_shadow(
    session: Session,
    links: list[ProgolSlateMatchModel],
    *,
    assume_gate_enabled: bool,
    assume_calibrator_available: bool,
) -> dict[str, Any]:
    run, snaps = load_active_run_snapshots(session)
    latest_predictions = _latest_predictions_by_match(session)
    rows = [
        _row_for_link(
            link,
            snaps=snaps,
            latest_predictions=latest_predictions,
            assume_gate_enabled=assume_gate_enabled,
            assume_calibrator_available=assume_calibrator_available,
        )
        for link in links
    ]
    return {
        "active_run": {
            "run_id": run.id,
            "algorithm_version": run.algorithm_version,
            "status": run.status,
            "snapshot_count": len(snaps),
        },
        "gate_config": {
            "team_rating_gate_enabled": settings.team_rating_gate_enabled,
            "team_rating_gate_competitions": settings.team_rating_gate_competitions,
            "require_both_medium_plus": settings.team_rating_gate_require_both_medium_plus,
            "require_calibrator": settings.team_rating_gate_require_calibrator,
            "productive_calibrator_available": (
                GATE_CALIBRATOR_METADATA.productive_calibrator_available
            ),
            "assume_gate_enabled": assume_gate_enabled,
            "assume_calibrator_available": assume_calibrator_available,
        },
        "summary": _summarize(rows),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow-only team-rating gate audit.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--draw-code")
    mode.add_argument("--competition")
    parser.add_argument("--assume-gate-enabled", action="store_true")
    parser.add_argument("--assume-calibrator-available", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        try:
            links = _links_for_scope(
                session,
                draw_code=args.draw_code,
                competition=args.competition,
            )
            report = audit_shadow(
                session,
                links,
                assume_gate_enabled=args.assume_gate_enabled,
                assume_calibrator_available=args.assume_calibrator_available,
            )
            report["scope"] = args.draw_code or args.competition
        finally:
            session.rollback()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
