"""R7.0 — Learning dataset readiness audit (read-only, never trains).

Answers the single gating question of the learning loop: *is there enough clean,
comparable evidence to justify training or adjusting a model?* It counts the
comparable slates and matches, how many carry full features / a rating / a canary
adjustment / a money-mode decision, and how many are excluded and why — then
returns a conservative ``training_ready`` verdict.

It NEVER trains and NEVER marks ``training_ready=true`` while results are
missing, conflicts are high, or there are too few labelled rows.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchFeatureSnapshotModel,
    ProgolJornadaScoreModel,
    ProgolSlateModel,
)
from app.models.team_rating import TeamRatingSnapshotModel
from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.services.learning_slate_scoring_service import _audit_probs
from app.services.slate_classification_service import classify_slate

# Conservative minimums before training/adjustment is even worth proposing.
MIN_COMPARABLE_SLATES = 8
MIN_COMPARABLE_MATCHES = 112  # ~8 weekend jornadas
MAX_CONFLICT_RATIO = 0.05
MIN_CLASSIFICATION_MATCHES = 20


def _teams_with_rating(session: Session) -> set[str]:
    return set(session.scalars(select(TeamRatingSnapshotModel.team_id).distinct()).all())


def _matches_with_features(session: Session, match_ids: list[str]) -> set[str]:
    if not match_ids:
        return set()
    rows = session.scalars(
        select(MatchFeatureSnapshotModel.match_id)
        .where(MatchFeatureSnapshotModel.match_id.in_(match_ids))
        .distinct()
    ).all()
    return set(rows)


def _latest_scores_by_slate(session: Session) -> dict[str, ProgolJornadaScoreModel]:
    ranked = (
        select(
            ProgolJornadaScoreModel.id.label("score_id"),
            func.row_number()
            .over(
                partition_by=ProgolJornadaScoreModel.slate_id,
                order_by=(ProgolJornadaScoreModel.computed_at.desc(), ProgolJornadaScoreModel.id.desc()),
            )
            .label("rn"),
        )
        .subquery()
    )
    rows = session.scalars(
        select(ProgolJornadaScoreModel)
        .join(ranked, ProgolJornadaScoreModel.id == ranked.c.score_id)
        .where(ranked.c.rn == 1)
    ).all()
    return {score.slate_id: score for score in rows}


def _is_sign_only_classification_detail(detail: dict[str, Any]) -> bool:
    return (
        detail.get("result_is_canonical") is False
        and detail.get("result_code") in {"1", "X", "2"}
        and detail.get("home_goals") is None
        and detail.get("away_goals") is None
    )


def _classification_row_count_from_score(
    score: ProgolJornadaScoreModel | None,
    *,
    canonical_ids: set[str],
    conflict_ids: set[str],
) -> int:
    """Fast count equivalent for AdaptiveDatasetService.build_rows_for_slate.

    Readiness only needs to know whether a completed slate has one trainable
    classification row per match. Building full Pydantic rows here makes the
    Aprendizaje tab slow as the slate history grows, so this reads the already
    materialized jornada score details and applies the same row gates.
    """
    if score is None or not score.composition_hash or not score.is_complete:
        return 0
    try:
        details: list[dict[str, Any]] = json.loads(score.details_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return 0

    count = 0
    for detail in details:
        match_id = detail.get("match_id")
        if not match_id or match_id in conflict_ids:
            continue
        if detail.get("result_code") is None:
            continue
        if detail.get("recommended_outcome") is None:
            continue
        if detail.get("result_is_canonical") is True or match_id in canonical_ids:
            count += 1
            continue
        if _is_sign_only_classification_detail(detail):
            count += 1
    return count


def build_dataset_readiness(session: Session) -> dict[str, Any]:
    from app.repositories.slate_repository import SlateRepository
    from app.services.learning_slate_scoring_service import LearningSlateScoringService
    from app.services.slate_service import SlateService

    service = SlateService(SlateRepository(session))
    slates: list[ProgolSlateModel] = service.list_slates(include_closed=True)
    scorer = LearningSlateScoringService(session)
    rated_teams = _teams_with_rating(session)
    latest_scores = _latest_scores_by_slate(session)

    comparable_slates: list[str] = []
    classification_slates: list[str] = []
    excluded: dict[str, str] = {}
    comparable_matches = 0
    classification_matches = 0
    conflict_matches = 0
    with_features = 0
    with_rating = 0
    with_canary = 0
    with_money_mode = 0
    by_competition: dict[str, int] = {}

    for slate in slates:
        reality = classify_slate(session, slate)
        match_ids = [sm.match_id for sm in slate.matches]
        if not match_ids:
            excluded[slate.draw_code] = "no_matches"
            continue
        if not reality.comparable_with_results:
            excluded[slate.draw_code] = f"not_comparable_lineage ({reality.classification.value})"
            continue
        canonical = CanonicalResultRepository(session).get_with_conflict_info(match_ids)
        conflict_ids = {
            mid for mid in match_ids if mid in canonical and canonical[mid].is_conflicting
        }
        canonical_ids = {
            mid for mid in match_ids if mid in canonical and not canonical[mid].is_conflicting
        }
        conflicts = len(conflict_ids)
        covered = len(canonical_ids)
        conflict_matches += conflicts
        classification_rows = 0
        if conflicts == 0:
            classification_rows = _classification_row_count_from_score(
                latest_scores.get(slate.id),
                canonical_ids=canonical_ids,
                conflict_ids=conflict_ids,
            )
        if classification_rows == len(match_ids):
            classification_slates.append(slate.draw_code)
            classification_matches += classification_rows
        if covered < len(match_ids):
            if classification_rows == len(match_ids):
                excluded[slate.draw_code] = (
                    f"classification_only_results "
                    f"({covered}/{len(match_ids)} canonical, "
                    f"{classification_rows} classification, {conflicts} conflicts)"
                )
            else:
                excluded[slate.draw_code] = (
                    f"incomplete_results ({covered}/{len(match_ids)} canonical, {conflicts} conflicts)"
                )
            continue

        # Comparable slate confirmed.
        comparable_slates.append(slate.draw_code)
        predictions = scorer._latest_predictions(slate, match_ids)
        feature_matches = _matches_with_features(session, match_ids)
        # Historical behavior counted every comparable match as having a
        # money-mode decision because _money_mode_blocked always returns bool.
        # Keep the count, but avoid rebuilding Money Mode for every old slate.
        with_money_mode += len(match_ids)

        for sm in slate.matches:
            comparable_matches += 1
            comp = sm.match.competition.name
            by_competition[comp] = by_competition.get(comp, 0) + 1
            if sm.match_id in feature_matches:
                with_features += 1
            home = sm.match.home_team_id
            away = sm.match.away_team_id
            if home in rated_teams and away in rated_teams:
                with_rating += 1
            pred = predictions.get(sm.match_id)
            if pred is not None and _audit_probs(pred, "effective_probabilities"):
                with_canary += 1

    conflict_ratio = round(conflict_matches / comparable_matches, 4) if comparable_matches else 0.0

    minimum_missing: list[str] = []
    if len(comparable_slates) < MIN_COMPARABLE_SLATES:
        minimum_missing.append(
            f"need ≥{MIN_COMPARABLE_SLATES} comparable slates (have {len(comparable_slates)})"
        )
    if comparable_matches < MIN_COMPARABLE_MATCHES:
        minimum_missing.append(
            f"need ≥{MIN_COMPARABLE_MATCHES} comparable matches (have {comparable_matches})"
        )
    if conflict_ratio > MAX_CONFLICT_RATIO:
        minimum_missing.append(
            f"conflict ratio {conflict_ratio} exceeds {MAX_CONFLICT_RATIO}"
        )

    training_ready = not minimum_missing and comparable_matches > 0
    classification_training_ready = (
        classification_matches >= MIN_CLASSIFICATION_MATCHES
        and conflict_ratio <= MAX_CONFLICT_RATIO
    )

    if comparable_matches == 0:
        reason = "no comparable matches — no official results applied yet"
        recommended = (
            "apply official results for a finished slate (e.g. PG-2337 / PGM-800) "
            "via the guarded manual-results CLI, then re-run this audit"
        )
    elif minimum_missing:
        reason = "insufficient clean comparable evidence: " + "; ".join(minimum_missing)
        recommended = "accumulate more finished slates with validated official results"
    else:
        reason = "enough clean comparable evidence to PROPOSE training (still a manual, gated decision)"
        recommended = "run a shadow training experiment and review before any activation"

    return {
        "mode": "learning_dataset_readiness",
        "trains": False,
        "training_ready": training_ready,
        "classification_training_ready": classification_training_ready,
        "reason": reason,
        "minimum_missing": minimum_missing,
        "recommended_next_data_action": recommended,
        "comparable_slate_count": len(comparable_slates),
        "comparable_slates": comparable_slates,
        "comparable_match_count": comparable_matches,
        "classification_comparable_slate_count": len(classification_slates),
        "classification_comparable_slates": classification_slates,
        "classification_match_count": classification_matches,
        "conflict_match_count": conflict_matches,
        "conflict_ratio": conflict_ratio,
        "matches_with_features": with_features,
        "matches_with_rating": with_rating,
        "matches_with_canary": with_canary,
        "matches_with_money_mode": with_money_mode,
        "by_competition": by_competition,
        "excluded": excluded,
        "thresholds": {
            "min_comparable_slates": MIN_COMPARABLE_SLATES,
            "min_comparable_matches": MIN_COMPARABLE_MATCHES,
            "min_classification_matches": MIN_CLASSIFICATION_MATCHES,
            "max_conflict_ratio": MAX_CONFLICT_RATIO,
        },
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }
