from __future__ import annotations

from pydantic import BaseModel


class AdaptiveDatasetRow(BaseModel):
    """One training-ready row from a scored Progol jornada.

    Every row has a linked prediction from the slate's current
    composition_hash and either a canonical scored result or an official
    Progol sign-only final usable for classification. Rows without a
    result or with conflicting sources are never included.
    """

    slate_id: str
    draw_code: str
    week_type: str
    composition_hash: str
    slate_version: int | None
    match_id: str
    position: int | None
    home_team: str
    away_team: str
    competition: str
    # Prediction probabilities (from the latest prediction for this slate/hash)
    prob_home: float | None
    prob_draw: float | None
    prob_away: float | None
    recommended_outcome: str | None
    confidence_band: str | None
    blocked_reason: str | None
    # Actual outcome
    actual_result: str        # "1", "X", or "2"
    home_goals: int | None
    away_goals: int | None
    # Derived metrics
    hit: bool | None
    brier_score: float | None
    result_is_canonical: bool
    # Ticket recommendation picks from the snapshot (null if no valid snapshot)
    ticket_pick_simple: list[str] | None
    ticket_pick_doubles: list[str] | None
    ticket_pick_full: list[str] | None
    ticket_hit_simple: bool | None
    ticket_hit_doubles: bool | None
    ticket_hit_full: bool | None


class ConfidenceBandDatasetStats(BaseModel):
    total: int
    hits: int
    hit_rate: float | None


class AdaptiveDatasetSummary(BaseModel):
    """Aggregate statistics across all scored jornadas in the dataset."""

    total_slates_scored: int
    total_slates_complete: int
    total_rows: int
    classification_trainable_rows: int = 0
    xgboost_trainable_rows: int = 0
    rows_with_canonical_result: int
    rows_with_sign_only_result: int = 0
    rows_with_conflict: int
    rows_with_ticket_info: int
    hit_rate: float | None
    brier_score_avg: float | None
    by_confidence_band: dict[str, ConfidenceBandDatasetStats]
