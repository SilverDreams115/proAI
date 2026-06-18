"""Phase A — Seguimiento (tracking) + comparison schemas.

Read-only postmortem view that joins, per slate:
  - the ORIGINAL prediction (stored, faithful to what was issued),
  - the raw vs decision probability split + ticket strategy (recomputed
    live by the sanity layer — never persisted reliably),
  - the real canonical / live result,
and derives ``prediction_status`` (hit/miss/pending) and
``learning_status`` (ready/waiting_result/excluded).

Nothing here writes the DB, trains, promotes, or fabricates a result.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TrackingMatch(BaseModel):
    position: int
    home: str
    away: str
    competition: str
    # Original pick mapped to L/E/V (1->L, X->E, 2->V); None when the slate
    # has no linked prediction for this match.
    original_pick: str | None = None
    raw_probabilities: dict[str, float] | None = None
    decision_probabilities: dict[str, float] | None = None
    ticket_strategy: str | None = None
    # Provenance of the probability vectors (observability only — never
    # feeds hit/miss scoring or learning eligibility):
    #   persisted_sanity_audit    -> raw+decision read from the prediction's
    #                                stored sanity_audit_json (historical).
    #   recomputed_current_sanity -> no stored audit; raw+decision re-derived
    #                                with the CURRENT sanity layer (not historical).
    #   decision_only             -> recompute unavailable; raw is null, decision
    #                                falls back to the persisted probability column.
    probability_source: str = "decision_only"
    raw_probabilities_is_historical: bool = False
    decision_probabilities_is_historical: bool = False
    # Real result (only when ingested); never fabricated.
    actual_result: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    match_status: str  # "finished" | "live" | "pending"
    prediction_status: str  # "hit" | "miss" | "pending"
    learning_status: str  # "ready" | "waiting_result" | "excluded"
    excluded_from_training: bool = False
    exclusion_reason: str | None = None


class TrackingResponse(BaseModel):
    slate_id: str
    draw_code: str
    week_type: str
    status: str  # "open" | "closed" | "live" | "complete"
    total_matches: int
    finished_matches: int
    live_matches: int
    pending_matches: int
    scored_matches: int
    hits: int
    misses: int
    accuracy: float | None = None
    learning_rows_ready: int
    learning_rows_pending: int
    learning_rows_excluded: int
    has_conflicts: bool = False
    comparable_with_results: bool = False
    last_result_update: datetime | None = None
    matches: list[TrackingMatch]
