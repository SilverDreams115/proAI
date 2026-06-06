"""Strict contract for LLM-based narrative extraction (Fase 3.2).

The architecture rule is firm: the LLM is an *extractor*, never a
predictor. It reads a free-text source (news article, lineup post,
injury report) and emits a structured list of availability events. The
probability engine does not depend on the LLM; it only consumes the
extracted JSON.

This module defines:

- `ExtractedAvailability`: the only payload shape the probability engine
  trusts. Anything else gets discarded.
- `NarrativeExtractor`: the protocol providers must implement.
- `HeuristicNarrativeExtractor`: the in-tree fallback that does not
  require a model server. It wraps the existing keyword/regex logic so
  the system still works without an LLM connected.

To connect a real provider (Claude, OpenAI, local model) implement
`NarrativeExtractor.extract` returning the same payload shape. The
service layer enforces:

- JSON schema validation on every record returned.
- Confidence floor (default 0.7) before a record is allowed to move
  probabilities.
- Source URL must be present, so every claim is auditable.

Records that fail any check are dropped silently from the probability
chain; they may still be persisted as evidence for review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol


# Allowed values for the strict contract. The probability engine refuses
# rows whose status or category does not match these enums.
ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"out", "suspended", "doubtful", "rotation_risk", "available"}
)
ALLOWED_CATEGORIES: frozenset[str] = frozenset({"injury", "suspension", "rotation"})
ALLOWED_TEAM_SIDES: frozenset[str] = frozenset({"home", "away"})

# Confidence floor for a record to influence probabilities. Below this
# value the record is persisted as evidence but does not feed the model.
DEFAULT_CONFIDENCE_FLOOR = 0.7


@dataclass(frozen=True, slots=True)
class ExtractedAvailability:
    """One availability fact, in the only shape the engine trusts.

    Fields mirror what the heuristic engine already produces; the LLM
    adapter (when added) must match this shape exactly. Records are
    immutable so they can be safely passed between layers."""

    team_side: str  # "home" | "away"
    player_name: str
    status: str  # one of ALLOWED_STATUSES
    category: str  # one of ALLOWED_CATEGORIES
    detail: str
    confidence: float
    source_url: str
    source_title: str
    impact_score: float
    position: str | None = None


class NarrativeExtractor(Protocol):
    """Contract for any source of `ExtractedAvailability` records.

    The probability engine only ever calls `extract`. Replace the
    implementation, never the interface."""

    def extract(
        self,
        *,
        home_team: str,
        away_team: str,
        source_text: str,
        source_url: str,
        source_title: str,
    ) -> list[ExtractedAvailability]:
        """Return availability events grounded in the supplied source."""
        ...


def validate_record(record: Any) -> ExtractedAvailability | None:
    """Apply the strict-contract checks to a candidate record.

    Returns the `ExtractedAvailability` when every check passes, or None
    when the record is unsafe to feed into the probability chain. Used by
    adapters when a provider returns loosely-typed JSON.
    """
    if not isinstance(record, dict):
        return None
    team_side = str(record.get("team_side", "")).strip().lower()
    if team_side not in ALLOWED_TEAM_SIDES:
        return None
    status = str(record.get("status", "")).strip().lower()
    if status not in ALLOWED_STATUSES:
        return None
    category = str(record.get("category", "")).strip().lower()
    if category not in ALLOWED_CATEGORIES:
        return None
    player_name = str(record.get("player_name", "")).strip()
    if not player_name:
        return None
    source_url = str(record.get("source_url", "")).strip()
    if not source_url:
        return None
    try:
        confidence = float(record.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    try:
        impact_score = float(record.get("impact_score", 0.5))
    except (TypeError, ValueError):
        impact_score = 0.5
    return ExtractedAvailability(
        team_side=team_side,
        player_name=player_name,
        status=status,
        category=category,
        detail=str(record.get("detail", "")),
        confidence=max(min(confidence, 1.0), 0.0),
        source_url=source_url,
        source_title=str(record.get("source_title", "")),
        impact_score=max(min(impact_score, 1.0), 0.0),
        position=(str(record.get("position")).strip() or None) if record.get("position") else None,
    )


def filter_actionable(
    records: Iterable[ExtractedAvailability],
    *,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> list[ExtractedAvailability]:
    """Return only the records confident enough to move probabilities.

    Records below the floor remain useful as evidence (the caller still
    persists them) but they do not feed the probability chain. This is
    the safety valve the architecture demanded: an LLM hallucination at
    confidence 0.4 cannot quietly tilt a slate's predictions."""
    return [record for record in records if record.confidence >= confidence_floor]


class HeuristicNarrativeExtractor:
    """In-tree fallback wrapping the existing keyword/regex engine.

    Lets the system keep running without an LLM connected. Real LLM
    adapters live behind the same `NarrativeExtractor` protocol so the
    probability engine is provider-agnostic."""

    def extract(
        self,
        *,
        home_team: str,
        away_team: str,
        source_text: str,
        source_url: str,
        source_title: str,
    ) -> list[ExtractedAvailability]:
        # The heuristic does its job inside NarrativeInterpretationService
        # against the persisted match payload. Here we keep the interface
        # complete with a defensive no-op: when no LLM is connected, the
        # narrative service should be invoked directly. Returning an empty
        # list here forces callers to be explicit about which path runs.
        return []
