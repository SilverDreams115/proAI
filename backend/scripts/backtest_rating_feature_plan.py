"""Rating-feature backtest PLANNING harness (R4) — READ-ONLY, NO TRAINING.

This script does NOT train, calibrate, persist artifacts, touch the approval
gate, or change predictions. It only READS the current DB and reports, per
competition, whether a future rating-feature backtest would be worth running:

    python backend/scripts/backtest_rating_feature_plan.py
    python backend/scripts/backtest_rating_feature_plan.py --min-matches 30

Per-competition report fields (see ``docs/team_rating_design.md`` §5):
  competition, matches, results_complete, learning_ready,
  rating_medium_plus_rate, current_fallback_rate, current_usable_model_rate,
  candidate_for_backtest, blocker.

Ratings come from the pure R1 calculator over canonical results (same mapping
as ``compute_team_ratings.py``). xgboost approval / readiness come from the
existing ``ModelTrainingService`` (read-only calls only). Fallback / usable
rates come from the latest prediction per match (``sanity_audit_json``).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.team_rating import TeamRatingCalculator
from app.domain.team_rating import default_config
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import PredictionModel
from app.repositories.canonical_result_repository import CanonicalResultRepository


def _load_cli_helpers():
    """Import the shared mapping helpers from the compute CLI.

    Done lazily (and with a path shim) so this file works both under pytest
    (rootdir already on path) and when run directly as
    ``python backend/scripts/backtest_rating_feature_plan.py`` — where the
    backend/ dir must be added so ``scripts`` is importable.
    """
    import pathlib
    import sys

    backend_dir = str(pathlib.Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from scripts.compute_team_ratings import build_input_matches
    from scripts.compute_team_ratings import namespace_for_competition

    return build_input_matches, namespace_for_competition

# Priority candidates from the design doc (lower-cased substring match).
_PRIORITY_COMPETITIONS = (
    "copa libertadores",
    "international friendlies",
    "brasileirao",  # control: rating must not regress it
)
# Coverage floor for a competition to be a backtest candidate (design §5).
_MIN_MEDIUM_PLUS_RATE = 0.80


@dataclass
class CompetitionPlan:
    competition: str
    matches: int
    results_complete: int
    learning_ready: bool
    rating_medium_plus_rate: float
    current_fallback_rate: float | None
    current_usable_model_rate: float | None
    xgboost_readiness: str
    is_priority_candidate: bool
    candidate_for_backtest: bool
    blocker: str


def _latest_predictions_by_match(session: Session) -> dict[str, PredictionModel]:
    """Latest prediction per match_id (by generated_at). Read-only."""
    rows = session.scalars(
        select(PredictionModel).order_by(PredictionModel.generated_at.asc())
    )
    latest: dict[str, PredictionModel] = {}
    for pred in rows:
        latest[pred.match_id] = pred  # ascending → last write wins = latest
    return latest


def _fallback_used(pred: PredictionModel) -> bool | None:
    if not pred.sanity_audit_json:
        return None
    try:
        audit = json.loads(pred.sanity_audit_json)
    except (ValueError, TypeError):
        return None
    return bool(audit.get("fallback_used", False))


def build_plan(session: Session, *, min_matches: int) -> list[dict[str, Any]]:
    build_input_matches, namespace_for_competition = _load_cli_helpers()
    config = default_config()
    input_matches, _teams, _prefilter, _considered = build_input_matches(session)
    snapshots, _summary = TeamRatingCalculator(config).compute(input_matches)

    # Competition + match metadata for grouping.
    match_rows = session.execute(
        select(
            MatchModel.id,
            MatchModel.home_team_id,
            MatchModel.away_team_id,
            CompetitionModel.name,
        ).join(CompetitionModel, MatchModel.competition_id == CompetitionModel.id)
    ).all()

    # Canonical (non-conflicting) results per competition.
    all_match_ids = [
        r[0] for r in session.execute(select(MatchResultModel.match_id).distinct())
    ]
    canonical = CanonicalResultRepository(session).get_canonical_for_matches(all_match_ids)

    latest_preds = _latest_predictions_by_match(session)

    @dataclass
    class _Agg:
        matches: int = 0
        results_complete: int = 0
        both_medium_plus: int = 0
        fallback_yes: int = 0
        audit_total: int = 0

    aggs: dict[str, _Agg] = {}

    def _medium_plus(team_id: str, namespace: str) -> bool:
        snap = snapshots.get((team_id, namespace))
        return snap is not None and snap.matches_count >= 4

    for match_id, home_id, away_id, comp_name in match_rows:
        agg = aggs.setdefault(comp_name, _Agg())
        agg.matches += 1
        if match_id in canonical:
            agg.results_complete += 1
        namespace = namespace_for_competition(comp_name)
        if _medium_plus(home_id, namespace) and _medium_plus(away_id, namespace):
            agg.both_medium_plus += 1
        pred = latest_preds.get(match_id)
        if pred is not None:
            fb = _fallback_used(pred)
            if fb is not None:
                agg.audit_total += 1
                if fb:
                    agg.fallback_yes += 1

    plans: list[CompetitionPlan] = []
    for comp_name, agg in aggs.items():
        learning_ready = agg.results_complete >= min_matches
        mp_rate = round(agg.both_medium_plus / agg.matches, 3) if agg.matches else 0.0
        fallback_rate = (
            round(agg.fallback_yes / agg.audit_total, 3) if agg.audit_total else None
        )
        usable_rate = (
            round(1.0 - (agg.fallback_yes / agg.audit_total), 3)
            if agg.audit_total
            else None
        )
        is_priority = any(p in comp_name.lower() for p in _PRIORITY_COMPETITIONS)

        blocker = ""
        if not learning_ready:
            blocker = f"insufficient_results ({agg.results_complete} < {min_matches})"
        elif mp_rate < _MIN_MEDIUM_PLUS_RATE:
            blocker = f"thin_rating_coverage ({mp_rate} < {_MIN_MEDIUM_PLUS_RATE})"
        candidate = learning_ready and mp_rate >= _MIN_MEDIUM_PLUS_RATE

        plans.append(
            CompetitionPlan(
                competition=comp_name,
                matches=agg.matches,
                results_complete=agg.results_complete,
                learning_ready=learning_ready,
                rating_medium_plus_rate=mp_rate,
                current_fallback_rate=fallback_rate,
                current_usable_model_rate=usable_rate,
                xgboost_readiness=_xgboost_readiness(session, comp_name),
                is_priority_candidate=is_priority,
                candidate_for_backtest=candidate,
                blocker=blocker,
            )
        )

    # Priority candidates first, then by coverage, then by sample.
    plans.sort(
        key=lambda p: (
            not p.is_priority_candidate,
            -p.rating_medium_plus_rate,
            -p.results_complete,
        )
    )
    return [asdict(p) for p in plans]


_READINESS_CACHE: dict[str, str] = {}


def _xgboost_readiness(session: Session, competition_name: str) -> str:
    """Read-only competition readiness via the existing training service.

    Never trains. Falls back to ``"unknown"`` if the service cannot be
    constructed in this environment."""
    if competition_name in _READINESS_CACHE:
        return _READINESS_CACHE[competition_name]
    try:
        from app.repositories.entity_repository import EntityRepository
        from app.repositories.result_repository import ResultRepository
        from app.repositories.training_repository import TrainingRepository
        from app.services.model_training_service import ModelTrainingService

        service = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        policy = service.competition_operating_policy(competition_name)
        readiness = str(policy.get("competition_readiness", "unknown"))
    except Exception:  # pragma: no cover - planning harness must never crash
        readiness = "unknown"
    _READINESS_CACHE[competition_name] = readiness
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only rating-feature backtest planning harness."
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=30,
        help="results_complete needed to be learning_ready (default 30, the "
        "XGBoost min sample size).",
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help="only report the design's priority candidate competitions.",
    )
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        try:
            plan = build_plan(session, min_matches=args.min_matches)
        finally:
            session.rollback()  # hard read-only guarantee

    if args.priority_only:
        plan = [row for row in plan if row["is_priority_candidate"]]

    print(
        json.dumps(
            {
                "min_matches": args.min_matches,
                "competition_count": len(plan),
                "candidates": [r["competition"] for r in plan if r["candidate_for_backtest"]],
                "competitions": plan,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
