"""Read-only shadow audit for the inactive team-rating gate (R5.1/R5.2).

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
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import SessionLocal
from app.domain.team_rating_calibrator import apply_temperature_scaling
from app.domain.team_rating_calibrator import get_team_rating_calibrator_candidate
from app.domain.team_rating_calibrator import is_calibrator_candidate_compatible
from app.domain.team_rating_calibrator import TeamRatingCalibratorCandidate
from app.domain.team_rating_gate_config import CRITICAL_SANITY_BLOCKERS
from app.domain.team_rating_gate_config import GATE_CALIBRATOR_METADATA
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.repositories.slate_repository import SlateRepository
from app.services.team_rating_feature_service import build_rating_features
from app.services.team_rating_shadow_service import evaluate_team_rating_shadow_for_match
from app.services.team_rating_routing_policy import ALL_ROUTING_SANITY_FLAGS
from app.services.team_rating_routing_policy import CLI_ROUTING_POLICIES
from app.services.team_rating_routing_policy import evaluate_team_rating_routing_policy
from app.services.team_rating_routing_policy import normalize_routing_policy
from scripts.audit_rating_features import load_active_run_snapshots
from scripts.compute_team_ratings import namespace_for_competition

_RATING_GATE_BLOCKERS = frozenset(
    {
        "rating_not_present",
        "not_both_medium_plus",
        "home_confidence_too_low",
        "away_confidence_too_low",
    }
)


def _enforce_read_only_transaction(session: Session) -> None:
    """Make live PostgreSQL audit sessions fail fast on accidental writes."""

    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


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
    known = ALL_ROUTING_SANITY_FLAGS | set(CRITICAL_SANITY_BLOCKERS)
    return [flag for flag in sorted(flags) if flag in known]


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


def _scope_competition_name(links: list[ProgolSlateMatchModel]) -> tuple[str, list[str]]:
    competitions = sorted({link.match.competition.name for link in links})
    if len(competitions) == 1:
        return competitions[0], []
    if not competitions:
        return "", ["no_matches"]
    return ", ".join(competitions), ["mixed_competitions"]


def _candidate_for_scope(
    *,
    candidate_id: str | None,
    links: list[ProgolSlateMatchModel],
    routing_policy: str,
) -> tuple[TeamRatingCalibratorCandidate | None, bool, list[str]]:
    if candidate_id is None:
        return None, False, []
    candidate = get_team_rating_calibrator_candidate(candidate_id)
    competition_name, scope_blockers = _scope_competition_name(links)
    compatible, blockers = is_calibrator_candidate_compatible(
        candidate=candidate,
        competition_name=competition_name,
        subset="both_medium_plus_only",
        routing_policy=routing_policy,
        min_test_rows=settings.team_rating_gate_min_test_rows,
    )
    all_blockers = [*scope_blockers, *blockers]
    return candidate, compatible and not scope_blockers, all_blockers


def _calibrated_probability_vector(
    pred: PredictionModel | None,
    candidate: TeamRatingCalibratorCandidate | None,
    *,
    candidate_available: bool,
    candidate_compatible: bool,
) -> dict[str, float] | None:
    if pred is None or candidate is None or not candidate_available or not candidate_compatible:
        return None
    if candidate.method != "temperature_scaling":
        return None
    return apply_temperature_scaling(
        {
            "home": pred.home_probability,
            "draw": pred.draw_probability,
            "away": pred.away_probability,
        },
        candidate.temperature,
    )


def _row_for_link(
    link: ProgolSlateMatchModel,
    *,
    snaps: dict[tuple[str, str], Any],
    latest_predictions: dict[str, PredictionModel],
    assume_gate_enabled: bool,
    assume_calibrator_available: bool,
    routing_policy: str,
    calibrator_candidate: TeamRatingCalibratorCandidate | None,
    calibrator_candidate_available: bool,
    calibrator_candidate_compatible: bool,
) -> dict[str, Any]:
    match = link.match
    facts = _rating_facts(snaps, match)
    latest_prediction = latest_predictions.get(match.id)
    sanity_flags = _legacy_sanity_flags(latest_prediction)
    decision = evaluate_team_rating_shadow_for_match(
        competition_name=match.competition.name,
        rating_present=facts["rating_present"],
        both_rating_medium_plus=facts["both_rating_medium_plus"],
        home_rating_confidence=facts["home_rating_confidence"],
        away_rating_confidence=facts["away_rating_confidence"],
        rating_diff=facts["rating_diff"],
        sanity_flags=[],
        assume_gate_enabled=assume_gate_enabled,
        assume_calibrator_available=assume_calibrator_available,
    )
    routing = evaluate_team_rating_routing_policy(
        policy=routing_policy,
        gate_eligible_if_enabled=decision.eligible_if_enabled,
        gate_blockers=decision.blockers,
        both_medium_plus=decision.both_medium_plus,
        calibrator_available=decision.calibrator_available,
        sanity_flags=sanity_flags,
    )
    decision_dict = asdict(decision)
    decision_dict["gate_blockers"] = decision_dict.pop("blockers")
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
        **decision_dict,
        "routing_policy": routing.policy,
        "would_use_rating_model": routing.eligible_for_rating_route,
        "would_remain_fallback": not routing.eligible_for_rating_route,
        "blockers": routing.blockers,
        "hard_sanity_blockers": routing.hard_sanity_blockers,
        "soft_sanity_blockers": routing.soft_sanity_blockers,
        "review_blockers": routing.review_blockers,
        "warnings": routing.warnings,
        "calibrated_probability_vector": _calibrated_probability_vector(
            latest_prediction,
            calibrator_candidate,
            candidate_available=calibrator_candidate_available,
            candidate_compatible=calibrator_candidate_compatible,
        ),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _positions(pred) -> list[int]:
        return [int(row["position"]) for row in rows if pred(row)]

    blocked_by_rating = sum(
        1
        for row in rows
        if any(blocker in _RATING_GATE_BLOCKERS for blocker in row["gate_blockers"])
    )
    blocked_by_calibrator = sum(
        1
        for row in rows
        if "calibrator_unavailable" in row["gate_blockers"]
        and "competition_not_allowed" not in row["gate_blockers"]
        and not any(blocker in _RATING_GATE_BLOCKERS for blocker in row["gate_blockers"])
    )
    blocked_by_hard = sum(1 for row in rows if row["hard_sanity_blockers"])
    blocked_by_soft = sum(1 for row in rows if row["soft_sanity_blockers"])
    blocked_by_review = sum(1 for row in rows if row["review_blockers"])
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
            1 for row in rows if "competition_not_allowed" in row["gate_blockers"]
        ),
        "blocked_by_rating": blocked_by_rating,
        "blocked_by_calibrator": blocked_by_calibrator,
        "blocked_by_hard_sanity": blocked_by_hard,
        "blocked_by_soft_sanity": blocked_by_soft,
        "blocked_by_review": blocked_by_review,
        "blocked_by_sanity": sum(
            1
            for row in rows
            if row["eligible_if_enabled"]
            and not row["would_use_rating_model"]
            and (
                row["hard_sanity_blockers"]
                or row["soft_sanity_blockers"]
                or row["review_blockers"]
            )
        ),
        "warnings": sum(1 for row in rows if row["warnings"]),
        "positions_eligible_if_enabled": _positions(lambda row: row["eligible_if_enabled"]),
        "positions_would_route": _positions(lambda row: row["would_use_rating_model"]),
        "positions_blocked": _positions(lambda row: not row["would_use_rating_model"]),
    }


def audit_shadow(
    session: Session,
    links: list[ProgolSlateMatchModel],
    *,
    assume_gate_enabled: bool,
    assume_calibrator_available: bool,
    routing_policy: str = "strict",
    calibrator_candidate_id: str | None = None,
    assume_calibrator_candidate_available: bool = False,
) -> dict[str, Any]:
    selected_policy = normalize_routing_policy(routing_policy)
    calibrator_candidate, calibrator_compatible, calibrator_blockers = (
        _candidate_for_scope(
            candidate_id=calibrator_candidate_id,
            links=links,
            routing_policy=selected_policy,
        )
    )
    candidate_available = (
        assume_calibrator_candidate_available
        and calibrator_candidate is not None
        and calibrator_compatible
    )
    effective_assume_calibrator_available = (
        assume_calibrator_available or candidate_available
    )
    run, snaps = load_active_run_snapshots(session)
    latest_predictions = _latest_predictions_by_match(session)
    rows = [
        _row_for_link(
            link,
            snaps=snaps,
            latest_predictions=latest_predictions,
            assume_gate_enabled=assume_gate_enabled,
            assume_calibrator_available=effective_assume_calibrator_available,
            routing_policy=selected_policy,
            calibrator_candidate=calibrator_candidate,
            calibrator_candidate_available=candidate_available,
            calibrator_candidate_compatible=calibrator_compatible,
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
            "assume_calibrator_candidate_available": (
                assume_calibrator_candidate_available
            ),
            "routing_policy": selected_policy,
            "calibrator_candidate_id": calibrator_candidate_id,
            "calibrator_compatible": calibrator_compatible,
            "calibrator_compatibility_blockers": calibrator_blockers,
            "calibrator_candidate_available": candidate_available,
        },
        "calibrator_candidate": (
            calibrator_candidate.as_dict() if calibrator_candidate is not None else None
        ),
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
    parser.add_argument(
        "--routing-policy",
        default="strict",
        choices=CLI_ROUTING_POLICIES,
    )
    parser.add_argument("--calibrator-candidate")
    parser.add_argument("--assume-calibrator-candidate-available", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        try:
            _enforce_read_only_transaction(session)
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
                routing_policy=args.routing_policy,
                calibrator_candidate_id=args.calibrator_candidate,
                assume_calibrator_candidate_available=(
                    args.assume_calibrator_candidate_available
                ),
            )
            report["scope"] = args.draw_code or args.competition
        finally:
            session.rollback()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
