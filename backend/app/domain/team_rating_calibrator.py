"""Team-rating calibrator candidates for shadow audits (R5.3).

Metadata and helpers in this module are intentionally inert: no DB
registration, no artifact loading, and no production routing. Auditors may use
the candidate to simulate whether a future controlled gate has a compatible
calibrator available.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

_EPSILON = 1e-12


@dataclass(frozen=True)
class TeamRatingCalibratorCandidate:
    candidate_id: str
    competition: str
    subset: str
    method: str
    temperature: float
    routing_policy: str
    source_experiment_commit: str
    source_validation_commit: str
    heldout_validation_commit: str
    test_rows: int
    baseline_brier: float
    calibrated_brier: float
    baseline_logloss: float
    calibrated_logloss: float
    baseline_ece: float
    calibrated_ece: float
    productive_available: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


INTERNATIONAL_FRIENDLIES_TEMPERATURE_V1 = TeamRatingCalibratorCandidate(
    candidate_id="international_friendlies_temperature_v1",
    competition="International Friendlies",
    subset="both_medium_plus_only",
    method="temperature_scaling",
    temperature=2.22,
    routing_policy="rating_replaces_fallback",
    source_experiment_commit="7bb4a9a",
    source_validation_commit="857a173",
    heldout_validation_commit="7bb4a9a",
    test_rows=161,
    baseline_brier=0.7216,
    calibrated_brier=0.6347,
    baseline_logloss=1.3125,
    calibrated_logloss=1.0718,
    baseline_ece=0.2346,
    calibrated_ece=0.1074,
    productive_available=False,
)

TEAM_RATING_CALIBRATOR_CANDIDATES: Mapping[str, TeamRatingCalibratorCandidate] = (
    MappingProxyType(
        {
            INTERNATIONAL_FRIENDLIES_TEMPERATURE_V1.candidate_id: (
                INTERNATIONAL_FRIENDLIES_TEMPERATURE_V1
            )
        }
    )
)


def apply_temperature_scaling(
    probabilities: Mapping[str, float],
    temperature: float,
) -> dict[str, float]:
    """Apply probability-space temperature scaling and return normalized probs.

    Uses ``p ** (1 / T)`` followed by normalization. For ``T > 1`` this softens
    a peaked distribution; for ``0 < T < 1`` it sharpens it. Zero probabilities
    receive a tiny epsilon so the output remains a valid distribution without
    mutating the input mapping.
    """
    if temperature <= 0 or not isfinite(temperature):
        raise ValueError("temperature must be a finite positive number")
    if not probabilities:
        raise ValueError("probabilities must not be empty")

    cleaned: dict[str, float] = {}
    for key, value in probabilities.items():
        prob = float(value)
        if prob < 0 or not isfinite(prob):
            raise ValueError(f"invalid probability for {key!r}")
        cleaned[str(key)] = max(prob, _EPSILON)

    raw_total = sum(cleaned.values())
    if raw_total <= 0:
        raise ValueError("probabilities must contain positive mass")

    inverse_temperature = 1.0 / temperature
    scaled = {
        key: (value / raw_total) ** inverse_temperature
        for key, value in cleaned.items()
    }
    scaled_total = sum(scaled.values())
    if scaled_total <= 0:
        raise ValueError("scaled probabilities contain no positive mass")
    return {key: value / scaled_total for key, value in scaled.items()}


def get_team_rating_calibrator_candidate(
    candidate_id: str,
) -> TeamRatingCalibratorCandidate:
    try:
        return TEAM_RATING_CALIBRATOR_CANDIDATES[candidate_id]
    except KeyError as exc:
        raise ValueError(f"unknown team-rating calibrator candidate {candidate_id!r}") from exc


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def is_calibrator_candidate_compatible(
    *,
    candidate: TeamRatingCalibratorCandidate,
    competition_name: str,
    subset: str,
    routing_policy: str,
    min_test_rows: int,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if _normalize(candidate.competition) != _normalize(competition_name):
        blockers.append("competition_mismatch")
    if _normalize(candidate.subset) != _normalize(subset):
        blockers.append("subset_mismatch")
    if _normalize(candidate.routing_policy) != _normalize(routing_policy):
        blockers.append("routing_policy_mismatch")
    if candidate.method != "temperature_scaling":
        blockers.append("unsupported_method")
    if candidate.temperature <= 0:
        blockers.append("invalid_temperature")
    if candidate.test_rows < min_test_rows:
        blockers.append("insufficient_test_rows")
    if candidate.productive_available:
        blockers.append("candidate_marked_productive")
    return not blockers, blockers
