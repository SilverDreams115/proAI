"""Read-only audit of feature/data coverage for Progol slates.

Answers "why does the model fall back / block?" at the DATA layer, not the
weights layer. For each match it computes the CURRENT model feature vector
(``FeatureService.build_model_features`` — read-only, no snapshot persisted)
and reports which anchors are present vs default/missing, what each feature's
source table is, whether the competition is approved for the trained
(XGBoost) engine, and the resulting fallback reasons.

It never writes, never regenerates persisted predictions and never invokes
the booster to score (only the read-only engine-dispatch probe).

Usage::

    python backend/scripts/audit_feature_coverage.py --draw-code PG-2338
    python backend/scripts/audit_feature_coverage.py --slate-id <uuid>
    python backend/scripts/audit_feature_coverage.py --all-slates
    python backend/scripts/audit_feature_coverage.py --competition "International Friendlies"
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import MatchResultModel
from app.models.tables import PredictionModel
from app.repositories.entity_repository import EntityRepository
from app.repositories.feature_repository import FeatureRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.training_repository import TrainingRepository
from app.services.feature_service import FeatureService
from app.services.model_training_service import ModelTrainingService
from app.services.prediction_service import PredictionService

# The four count anchors whose 0.0 value means "no real data point".
_DATA_ANCHOR_KEYS = (
    "home_recent_matches",
    "away_recent_matches",
    "head_to_head_matches",
    "evidence_count",
)

# Static source map for the reported features (prefix-matched for families).
_FEATURE_SOURCES: dict[str, str] = {
    "home_recent": "match_results (ResultRepository.list_recent_team_results)",
    "away_recent": "match_results (ResultRepository.list_recent_team_results)",
    "home_points": "match_results",
    "away_points": "match_results",
    "home_goal": "match_results",
    "away_goal": "match_results",
    "home_goals": "match_results",
    "away_goals": "match_results",
    "form_gap": "match_results",
    "goal_balance_gap": "match_results",
    "rest_gap": "match_results",
    "head_to_head": "match_results (head-to-head)",
    "evidence_count": "evidence_items",
    "injury": "evidence_items / player_availability",
    "suspension": "evidence_items / player_availability",
    "rotation": "evidence_items / player_availability",
    "home_context": "player_availability",
    "away_context": "player_availability",
    "same_country": "teams.country",
    "venue_known": "matches.venue",
    "home_advantage": "constant (1.0)",
}

# Features whose 0.0 is the neutral "no data" default (constants excluded).
_CONSTANT_FEATURES = {"home_advantage"}


def _source_for(feature_key: str) -> str:
    for prefix, src in _FEATURE_SOURCES.items():
        if feature_key.startswith(prefix):
            return src
    return "feature_service (derived)"


@dataclass
class MatchCoverage:
    position: int
    home: str
    away: str
    competition: str
    feature_vector: dict[str, float]
    feature_sources: dict[str, str]
    null_features: list[str]
    default_features: list[str]
    fallback_reasons: list[str]
    evidence_count: float
    head_to_head_matches: float
    home_recent_matches: float
    away_recent_matches: float
    readiness: str
    engine: str
    competition_approved: bool
    insufficient_data: bool
    usable_for_trained_model: bool
    data_gap_tags: list[str]
    has_prediction: bool
    has_result: bool


def analyze_match_coverage(
    *,
    position: int,
    home: str,
    away: str,
    competition_name: str,
    feature_vector: dict[str, float],
    readiness: str,
    engine: str,
    competition_approved: bool,
    insufficient_data: bool,
    has_prediction: bool,
    has_result: bool,
) -> MatchCoverage:
    """Pure classification of one match's data coverage. No DB."""
    vec = {k: round(float(v), 4) for k, v in feature_vector.items()}
    home_recent = float(vec.get("home_recent_matches", 0.0))
    away_recent = float(vec.get("away_recent_matches", 0.0))
    h2h = float(vec.get("head_to_head_matches", 0.0))
    evidence = float(vec.get("evidence_count", 0.0))

    null_features = [k for k in _DATA_ANCHOR_KEYS if float(vec.get(k, 0.0)) == 0.0]
    default_features = [
        k
        for k, v in vec.items()
        if k not in _CONSTANT_FEATURES and float(v) == 0.0
    ]

    fallback_reasons: list[str] = []
    if engine != "xgboost":
        fallback_reasons.append("engine_is_heuristic_fallback")
    if not competition_approved:
        fallback_reasons.append("competition_not_xgboost_approved")
    if insufficient_data:
        fallback_reasons.append("insufficient_data_anchors")
    if home_recent == 0.0 or away_recent == 0.0:
        fallback_reasons.append("team_without_recent_results")
    if h2h == 0.0:
        fallback_reasons.append("no_head_to_head")
    if evidence == 0.0:
        fallback_reasons.append("no_contextual_evidence")

    data_gap_tags: list[str] = []
    if home_recent == 0.0 or away_recent == 0.0:
        data_gap_tags.append("data_missing_team_history")
    if 0.0 < (home_recent + away_recent) < 4.0:
        data_gap_tags.append("data_missing_recent_form")
    if h2h == 0.0:
        data_gap_tags.append("data_missing_h2h")
    if readiness in ("context_only", "not_ready", "unclassified"):
        data_gap_tags.append("data_missing_competition_history")
    if readiness == "context_only":
        data_gap_tags.append("data_context_only_by_policy")
    # The pipeline has no Elo/strength rating feature at all (structural).
    data_gap_tags.append("data_missing_ratings")

    return MatchCoverage(
        position=position,
        home=home,
        away=away,
        competition=competition_name,
        feature_vector=vec,
        feature_sources={k: _source_for(k) for k in vec},
        null_features=null_features,
        default_features=default_features,
        fallback_reasons=fallback_reasons,
        evidence_count=evidence,
        head_to_head_matches=h2h,
        home_recent_matches=home_recent,
        away_recent_matches=away_recent,
        readiness=readiness,
        engine=engine,
        competition_approved=competition_approved,
        insufficient_data=insufficient_data,
        usable_for_trained_model=competition_approved and not insufficient_data,
        data_gap_tags=data_gap_tags,
        has_prediction=has_prediction,
        has_result=has_result,
    )


def summarize(rows: list[MatchCoverage]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {"total_matches": 0}
    fallback = sum(1 for r in rows if r.engine != "xgboost")
    usable = sum(1 for r in rows if r.usable_for_trained_model)
    anchor_cells = len(_DATA_ANCHOR_KEYS) * total
    null_cells = sum(len(r.null_features) for r in rows)
    default_cells = sum(len(r.default_features) for r in rows)
    feature_cells = sum(len(r.feature_vector) for r in rows) or 1

    # Per-feature anchor coverage buckets across the slate.
    zero = partial = good = 0
    for key in _DATA_ANCHOR_KEYS:
        present = sum(1 for r in rows if float(r.feature_vector.get(key, 0.0)) > 0.0)
        frac = present / total
        if frac == 0.0:
            zero += 1
        elif frac < 0.6:
            partial += 1
        else:
            good += 1

    reason_counts: dict[str, int] = {}
    for r in rows:
        for reason in r.fallback_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        "total_matches": total,
        "usable_model_count": usable,
        "fallback_count": fallback,
        "fallback_rate": round(fallback / total, 3),
        "null_feature_rate": round(null_cells / anchor_cells, 3),
        "default_feature_rate": round(default_cells / feature_cells, 3),
        "features_with_zero_coverage": zero,
        "features_with_partial_coverage": partial,
        "features_with_good_coverage": good,
        "top_fallback_reasons": sorted(reason_counts.items(), key=lambda kv: -kv[1]),
    }


def _competition_table(rows: list[MatchCoverage]) -> list[dict[str, Any]]:
    by_comp: dict[str, list[MatchCoverage]] = {}
    for r in rows:
        by_comp.setdefault(r.competition, []).append(r)
    table = []
    for comp, group in by_comp.items():
        n = len(group)
        table.append({
            "competition": comp,
            "matches": n,
            "complete_results": sum(1 for r in group if r.has_result),
            "learning_ready": sum(1 for r in group if r.has_result and not r.insufficient_data),
            "fallback_rate": round(sum(1 for r in group if r.engine != "xgboost") / n, 3),
            "avg_evidence_count": round(sum(r.evidence_count for r in group) / n, 3),
            "avg_default_feature_rate": round(
                sum(len(r.default_features) / (len(r.feature_vector) or 1) for r in group) / n, 3
            ),
            "usable_model_rate": round(sum(1 for r in group if r.usable_for_trained_model) / n, 3),
        })
    return sorted(table, key=lambda d: (-d["usable_model_rate"], -d["matches"]))


@dataclass
class _Wiring:
    feature_service: FeatureService
    prediction_service: PredictionService
    training_service: ModelTrainingService
    approved: set[str]


def _wire(session: Session) -> _Wiring:
    training = ModelTrainingService(
        TrainingRepository(session), EntityRepository(session), ResultRepository(session)
    )
    feature = FeatureService(FeatureRepository(session), ResultRepository(session))
    prediction = PredictionService(training)
    try:
        approved = set(training._xgboost_approved_competitions())  # noqa: SLF001
    except Exception:
        approved = set()
    return _Wiring(feature, prediction, training, approved)


def _coverage_for_match(session: Session, wiring: _Wiring, link: Any) -> MatchCoverage:
    match = link.match
    fmap = wiring.feature_service.build_model_features(match)  # read-only
    policy = wiring.training_service.competition_operating_policy(match.competition.name)
    comp_key = wiring.training_service._competition_key(match.competition.name)  # noqa: SLF001
    engine = wiring.training_service.prediction_engine_for_match(match)
    insufficient = wiring.prediction_service._has_insufficient_data(fmap)  # noqa: SLF001
    has_pred = session.query(PredictionModel).filter(PredictionModel.match_id == match.id).count() > 0
    has_res = session.query(MatchResultModel).filter(MatchResultModel.match_id == match.id).count() > 0
    return analyze_match_coverage(
        position=link.position,
        home=match.home_team.name,
        away=match.away_team.name,
        competition_name=match.competition.name,
        feature_vector=fmap,
        readiness=str(policy.get("competition_readiness", "unclassified")),
        engine=engine,
        competition_approved=comp_key in wiring.approved,
        insufficient_data=insufficient,
        has_prediction=has_pred,
        has_result=has_res,
    )


def build_feature_coverage(
    session: Session,
    *,
    slate_id: str | None = None,
    draw_code: str | None = None,
    all_slates: bool = False,
    competition: str | None = None,
) -> dict[str, Any]:
    repo = SlateRepository(session)
    wiring = _wire(session)

    if all_slates or competition:
        slates = repo.list_slates()
    elif slate_id:
        s = repo.get_slate(slate_id)
        slates = [s] if s is not None else []
    elif draw_code:
        found = repo.find_by_draw_code(draw_code)
        slates = [repo.get_slate(found.id)] if found is not None else []
    else:
        raise ValueError("pass --slate-id / --draw-code / --all-slates / --competition")

    rows: list[MatchCoverage] = []
    per_slate: list[dict[str, Any]] = []
    for slate in slates:
        if slate is None:
            continue
        slate_rows = [
            _coverage_for_match(session, wiring, link)
            for link in sorted(slate.matches, key=lambda link: link.position)
        ]
        if competition:
            slate_rows = [r for r in slate_rows if r.competition == competition]
        if not slate_rows:
            continue
        rows.extend(slate_rows)
        per_slate.append({
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "summary": summarize(slate_rows),
            "rows": [asdict(r) for r in slate_rows],
        })

    historical = {
        "total_slates": len(per_slate),
        "total_matches": len(rows),
        "matches_with_predictions": sum(1 for r in rows if r.has_prediction),
        "matches_with_results": sum(1 for r in rows if r.has_result),
        "matches_learning_ready": sum(1 for r in rows if r.has_result and not r.insufficient_data),
        "matches_blocked": sum(1 for r in rows if r.insufficient_data),
        "matches_with_complete_features": sum(1 for r in rows if not r.insufficient_data),
        "matches_fallback": sum(1 for r in rows if r.engine != "xgboost"),
        "matches_non_fallback": sum(1 for r in rows if r.engine == "xgboost"),
    }

    return {
        "scope": "all_slates" if (all_slates or competition) else "single",
        "competition_filter": competition,
        "global_summary": summarize(rows),
        "historical": historical,
        "competition_table": _competition_table(rows),
        "slates": per_slate,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only feature/data coverage audit.")
    parser.add_argument("--slate-id")
    parser.add_argument("--draw-code")
    parser.add_argument("--all-slates", action="store_true")
    parser.add_argument("--competition")
    parser.add_argument("--summary-only", action="store_true", help="omit per-match rows")
    args = parser.parse_args()

    with SessionLocal() as session:
        report = build_feature_coverage(
            session,
            slate_id=args.slate_id,
            draw_code=args.draw_code,
            all_slates=args.all_slates,
            competition=args.competition,
        )
        session.rollback()  # hard read-only guarantee

    if args.summary_only:
        report = {k: v for k, v in report.items() if k != "slates"}
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
