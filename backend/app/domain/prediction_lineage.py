"""R7.6 — Prediction lineage contract.

Every prediction *persisted* for a slate must be fully traceable: linked to its
slate (``slate_id``), its fixture composition (``composition_hash`` +
``slate_version``), and carry the decision-time guardrail trace
(``sanity_audit_json`` with raw/display/decision vectors, evidence/risk/status,
policy version and model/fallback lineage).

Historical "blind" rows (predictions persisted before this contract — e.g. with
``slate_id`` NULL or no ``sanity_audit_json``) are left untouched; this contract
applies to **future** persisted predictions only.

Usage:
- ``check_prediction_lineage(...)`` → non-raising; returns the missing fields
  (used by read-only computes and the audit script).
- ``assert_prediction_lineage_complete(...)`` → raises ``PredictionLineageError``
  before a row is written, so production never persists a blind prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# sanity_audit_json sub-fields a persistable prediction must carry.
REQUIRED_AUDIT_VECTORS = (
    "raw_probabilities",
    "display_probabilities",
    "decision_probabilities",
)
REQUIRED_AUDIT_FIELDS = (
    "final_status",
    "evidence_level",
    "sanity_policy_version",
)


class PredictionLineageError(ValueError):
    """Raised when a prediction is missing the lineage required to persist it."""


@dataclass(frozen=True)
class LineageCheck:
    complete: bool
    missing: list[str] = field(default_factory=list)


def check_prediction_lineage(
    *,
    match_id: str | None,
    slate_id: str | None,
    composition_hash: str | None,
    slate_version: int | None,
    recommended_outcome: str | None,
    sanity_audit: dict[str, Any] | None,
) -> LineageCheck:
    """Non-raising lineage completeness check. Returns the missing field names."""
    missing: list[str] = []
    if not match_id:
        missing.append("match_id")
    if not slate_id:
        missing.append("slate_id")
    if not composition_hash:
        missing.append("composition_hash")
    if slate_version is None:
        missing.append("slate_version")
    if not recommended_outcome:
        missing.append("recommended_outcome")

    if not isinstance(sanity_audit, dict):
        missing.append("sanity_audit_json")
    else:
        for key in REQUIRED_AUDIT_VECTORS:
            vector = sanity_audit.get(key)
            if not isinstance(vector, dict) or not vector:
                missing.append(f"sanity_audit.{key}")
        for key in REQUIRED_AUDIT_FIELDS:
            if sanity_audit.get(key) in (None, ""):
                missing.append(f"sanity_audit.{key}")
        # Model lineage: a real artifact id OR an explicit fallback marker.
        if not sanity_audit.get("model_artifact_id") and not sanity_audit.get("fallback_used"):
            missing.append("sanity_audit.model_artifact_id|fallback_used")

    return LineageCheck(complete=not missing, missing=missing)


def assert_prediction_lineage_complete(
    *,
    match_id: str | None,
    slate_id: str | None,
    composition_hash: str | None,
    slate_version: int | None,
    recommended_outcome: str | None,
    sanity_audit: dict[str, Any] | None,
) -> None:
    """Raise PredictionLineageError if the prediction cannot be persisted."""
    check = check_prediction_lineage(
        match_id=match_id,
        slate_id=slate_id,
        composition_hash=composition_hash,
        slate_version=slate_version,
        recommended_outcome=recommended_outcome,
        sanity_audit=sanity_audit,
    )
    if not check.complete:
        raise PredictionLineageError(
            "Prediction lineage incomplete: missing " + ", ".join(check.missing)
        )
