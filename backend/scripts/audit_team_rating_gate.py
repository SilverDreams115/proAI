"""Read-only DRY-RUN audit of the inactive team-rating gate (R5.0).

Shows, per slate / per competition, what the controlled gate WOULD decide —
both today (flag OFF → everything blocked_by_flag) and under a hypothetical
future activation — WITHOUT changing anything. It evaluates the pure predicate
``evaluate_team_rating_gate`` against the latest active rating run and the
existing predictions' sanity flags. It writes nothing (rollback) and never
flips a flag.

Two hypothetical views are reported:
  * rating-guard-only: flag ON + calibrator available, IGNORING the legacy
    (fallback-era) sanity flags — answers "how many clear the rating guard?"
    (e.g. PG-2338 → up to 13/14, pos13 blocked by partial_rating);
  * full gate: additionally requires no critical sanity blocker on the current
    prediction — strictly more conservative.

Usage::

    python backend/scripts/audit_team_rating_gate.py --draw-code PG-2338
    python backend/scripts/audit_team_rating_gate.py --competition "International Friendlies"
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import SessionLocal
from app.domain.team_rating_gate_config import CRITICAL_SANITY_BLOCKERS
from app.domain.team_rating_gate_config import GATE_CALIBRATOR_METADATA
from app.models.tables import PredictionModel
from app.repositories.slate_repository import SlateRepository
from app.services.team_rating_gate_service import evaluate_team_rating_gate
from scripts.audit_rating_features import load_active_run_snapshots
from scripts.compute_team_ratings import namespace_for_competition


def _latest_predictions_by_match(session: Session) -> dict[str, PredictionModel]:
    rows = session.scalars(select(PredictionModel).order_by(PredictionModel.generated_at.asc()))
    latest: dict[str, PredictionModel] = {}
    for pred in rows:
        latest[pred.match_id] = pred
    return latest


def _legacy_sanity_flags(pred: PredictionModel | None) -> list[str]:
    """Critical sanity flags carried by the match's latest (fallback-era)
    prediction. Read-only; used only as an informational overlay."""
    if pred is None or not pred.sanity_audit_json:
        return []
    try:
        audit = json.loads(pred.sanity_audit_json)
    except (ValueError, TypeError):
        return []
    flags = {str(f).strip().upper() for f in audit.get("sanity_flags", [])}
    if bool(audit.get("fallback_used", False)):
        flags.add("FALLBACK_USED")
    if str(audit.get("evidence_level", "")).lower() == "low":
        flags.add("LOW_EVIDENCE")
    if str(audit.get("final_status", "")).upper() in {"BLOCKED", "REVISAR"}:
        flags.add(str(audit.get("final_status")).upper())
    return [f for f in flags if f in CRITICAL_SANITY_BLOCKERS]


def _match_facts(snaps, home_id: str, away_id: str, competition: str):
    ns = namespace_for_competition(competition)
    home = snaps.get((home_id, ns))
    away = snaps.get((away_id, ns))
    hc = home.matches_count if home else 0
    ac = away.matches_count if away else 0
    return {
        "namespace": ns,
        "rating_present": hc > 0 and ac > 0,
        "both_rating_medium_plus": hc >= 4 and ac >= 4,
        "home_rating_confidence": home.confidence_bucket if home else "no_rating",
        "away_rating_confidence": away.confidence_bucket if away else "no_rating",
        "home_matches_count": hc,
        "away_matches_count": ac,
    }


def _evaluate_match(facts: dict[str, Any], competition: str, legacy_flags: list[str]):
    # Current production state: flag OFF → flag_disabled.
    current = evaluate_team_rating_gate(
        competition_name=competition,
        rating_present=facts["rating_present"],
        both_rating_medium_plus=facts["both_rating_medium_plus"],
        home_rating_confidence=facts["home_rating_confidence"],
        away_rating_confidence=facts["away_rating_confidence"],
        calibrator_available=GATE_CALIBRATOR_METADATA.productive_calibrator_available,
        sanity_flags=legacy_flags,
        # explicit current flag state (defaults to settings anyway):
        feature_flag_enabled=settings.team_rating_gate_enabled,
    )
    # Hypothetical: flag ON + calibrator available, IGNORING legacy sanity.
    guard = evaluate_team_rating_gate(
        competition_name=competition,
        rating_present=facts["rating_present"],
        both_rating_medium_plus=facts["both_rating_medium_plus"],
        home_rating_confidence=facts["home_rating_confidence"],
        away_rating_confidence=facts["away_rating_confidence"],
        calibrator_available=True,
        sanity_flags=[],
        feature_flag_enabled=True,
    )
    # Hypothetical full gate: flag ON + calibrator + legacy sanity considered.
    full = evaluate_team_rating_gate(
        competition_name=competition,
        rating_present=facts["rating_present"],
        both_rating_medium_plus=facts["both_rating_medium_plus"],
        home_rating_confidence=facts["home_rating_confidence"],
        away_rating_confidence=facts["away_rating_confidence"],
        calibrator_available=True,
        sanity_flags=legacy_flags,
        feature_flag_enabled=True,
    )
    return current, guard, full


def _aggregate(matches: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(matches)
    comp_matches = sum(1 for m in matches if m["in_gate_competition"])
    return {
        "total_matches": n,
        "competition_matches": comp_matches,
        "gate_enabled": settings.team_rating_gate_enabled,
        "calibrator_available": GATE_CALIBRATOR_METADATA.productive_calibrator_available,
        "eligible_current": sum(1 for m in matches if m["current_eligible"]),
        "blocked_by_flag": sum(1 for m in matches if "flag_disabled" in m["current_blockers"]),
        "eligible_if_enabled_rating_guard": sum(1 for m in matches if m["guard_eligible"]),
        "would_route_to_rating_model": sum(1 for m in matches if m["full_eligible"]),
        "would_remain_fallback": n - sum(1 for m in matches if m["full_eligible"]),
        # blocker breakdown under the rating-guard view (flag ON, calibrator on)
        "blocked_by_competition": sum(1 for m in matches if "competition_not_allowed" in m["guard_blockers"]),
        "blocked_by_missing_rating": sum(1 for m in matches if "rating_not_present" in m["guard_blockers"]),
        "blocked_by_not_both_medium_plus": sum(1 for m in matches if "not_both_medium_plus" in m["guard_blockers"]),
        "blocked_by_low_confidence": sum(
            1 for m in matches
            if "home_confidence_too_low" in m["guard_blockers"] or "away_confidence_too_low" in m["guard_blockers"]
        ),
        # current productive calibrator is unavailable → this many rating-guard
        # passers would still be held back until a calibrator is wired in
        "blocked_by_missing_calibrator_today": sum(
            1 for m in matches if m["guard_eligible"] and not GATE_CALIBRATOR_METADATA.productive_calibrator_available
        ),
        # informational: among rating-guard passers, how many carry a legacy
        # critical sanity flag (so the full gate would still hold them).
        "blocked_by_sanity": sum(1 for m in matches if m["guard_eligible"] and not m["full_eligible"]),
    }


def audit_matches(session: Session, snaps, links, *, gate_competitions: set[str]) -> dict[str, Any]:
    latest = _latest_predictions_by_match(session)
    rows: list[dict[str, Any]] = []
    for link in links:
        m = link.match
        comp = m.competition.name
        facts = _match_facts(snaps, m.home_team_id, m.away_team_id, comp)
        legacy = _legacy_sanity_flags(latest.get(m.id))
        current, guard, full = _evaluate_match(facts, comp, legacy)
        rows.append({
            "position": getattr(link, "position", None),
            "home": m.home_team.name, "away": m.away_team.name,
            "competition": comp,
            "in_gate_competition": comp.strip().lower() in gate_competitions,
            **facts,
            "legacy_sanity_flags": legacy,
            "current_eligible": current.eligible, "current_blockers": current.blockers,
            "guard_eligible": guard.eligible, "guard_blockers": guard.blockers,
            "full_eligible": full.eligible, "full_blockers": full.blockers,
        })
    return {"summary": _aggregate(rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run audit of the inactive team-rating gate.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--draw-code")
    mode.add_argument("--competition")
    args = parser.parse_args(argv)

    gate_comps = {c.strip().lower() for c in settings.team_rating_gate_competitions}
    with SessionLocal() as session:
        try:
            _run, snaps = load_active_run_snapshots(session)
            repo = SlateRepository(session)
            if args.draw_code:
                found = repo.find_by_draw_code(args.draw_code)
                slate = repo.get_slate(found.id) if found else None
                if slate is None:
                    raise SystemExit(f"draw_code {args.draw_code!r} not found")
                links = sorted(slate.matches, key=lambda lnk: lnk.position)
            else:
                links = []
                for slate in repo.list_slates():
                    for link in slate.matches:
                        if link.match.competition.name.strip().lower() == args.competition.strip().lower():
                            links.append(link)
            report = audit_matches(session, snaps, links, gate_competitions=gate_comps)
            report["scope"] = args.draw_code or args.competition
            report["gate_config"] = {
                "team_rating_gate_enabled": settings.team_rating_gate_enabled,
                "team_rating_gate_competitions": settings.team_rating_gate_competitions,
                "require_both_medium_plus": settings.team_rating_gate_require_both_medium_plus,
                "require_calibrator": settings.team_rating_gate_require_calibrator,
            }
        finally:
            session.rollback()  # hard read-only guarantee
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
