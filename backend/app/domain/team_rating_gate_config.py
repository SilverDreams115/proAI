"""Experimental calibrator metadata for the team-rating gate (R5.0).

This is METADATA ONLY. It records what the R4.2 held-out validation produced
so the (inactive) gate predicate and the dry-run auditor can describe a future
activation. It loads NO calibrator weights, registers NOTHING in the DB, and
changes NO probabilities. The numbers below come from the offline experiment
``backend/scripts/validate_rating_candidate.py`` at commit 7bb4a9a.

Sanity flags considered hard blockers by the gate (a confident rating must
never override the existing low-evidence guardrails).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

# Confidence buckets accepted as "confident enough" for the gate.
CONFIDENT_BUCKETS = ("medium", "strong")

# Sanity-layer flags that must block the gate regardless of rating quality.
CRITICAL_SANITY_BLOCKERS = (
    "LOW_EVIDENCE",
    "FALLBACK_USED",
    "BLOCKED",
    "REVISAR",
    "EXTREME_PROBABILITY_WITHOUT_EVIDENCE",
)


@dataclass(frozen=True)
class GateCalibratorMetadata:
    """Read-only record of the experimental calibrator behind the gate.

    NOT a productive calibrator: no weights, no isotonic/temperature object is
    loaded from here. A real activation must refit a calibrator on a rolling
    calibration window; these fields only document the validating experiment.
    """

    competition: str = "International Friendlies"
    subset: str = "both_medium_plus_only"
    algorithm_version: str = "elo_v1"
    method: str = "temperature_scaling"
    temperature: float = 2.22
    source_experiment_commit: str = "7bb4a9a"
    test_rows: int = 161
    # Held-out test-fold metrics (with_rating_temperature_calibrated vs baseline).
    test_brier: float = 0.6347
    baseline_brier: float = 0.7216
    test_log_loss: float = 1.0718
    baseline_log_loss: float = 1.3125
    test_ece: float = 0.1074
    baseline_ece: float = 0.2346
    verdict: str = "ready_for_controlled_gate_design"
    # A productive calibrator is NOT available until one is refit and wired in.
    productive_calibrator_available: bool = False
    notes: tuple[str, ...] = field(
        default_factory=lambda: (
            "Metadata only; no weights loaded, no DB registration.",
            "Activation must refit a calibrator on a rolling window.",
            "Brasileirao stays a non-regression control.",
        )
    )


GATE_CALIBRATOR_METADATA = GateCalibratorMetadata()
