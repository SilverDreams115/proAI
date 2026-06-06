"""Tests for the strict LLM extractor contract (Fase 3.2)."""
from __future__ import annotations

from app.services.narrative_extractor import (
    DEFAULT_CONFIDENCE_FLOOR,
    ExtractedAvailability,
    HeuristicNarrativeExtractor,
    filter_actionable,
    validate_record,
)


def _well_formed_record(**overrides) -> dict:
    """A record that passes every validation check; tests override one
    field at a time to assert which violations get rejected."""
    base = {
        "team_side": "home",
        "player_name": "Pedro Lopez",
        "status": "out",
        "category": "injury",
        "detail": "torn ACL",
        "confidence": 0.85,
        "source_url": "https://example.com/article",
        "source_title": "Injury report",
        "impact_score": 0.8,
        "position": "midfielder",
    }
    base.update(overrides)
    return base


def test_validate_accepts_complete_record() -> None:
    record = validate_record(_well_formed_record())
    assert isinstance(record, ExtractedAvailability)
    assert record.player_name == "Pedro Lopez"
    assert record.confidence == 0.85


def test_validate_rejects_unknown_team_side() -> None:
    assert validate_record(_well_formed_record(team_side="middle")) is None


def test_validate_rejects_unknown_status() -> None:
    assert validate_record(_well_formed_record(status="injured-ish")) is None


def test_validate_rejects_unknown_category() -> None:
    assert validate_record(_well_formed_record(category="unknown")) is None


def test_validate_rejects_missing_player_name() -> None:
    assert validate_record(_well_formed_record(player_name="")) is None


def test_validate_rejects_missing_source_url() -> None:
    """The contract forces a source URL so every claim is auditable."""
    assert validate_record(_well_formed_record(source_url="")) is None


def test_validate_rejects_non_numeric_confidence() -> None:
    assert validate_record(_well_formed_record(confidence="high")) is None


def test_validate_clips_confidence_into_unit_interval() -> None:
    record = validate_record(_well_formed_record(confidence=1.4))
    assert record is not None
    assert record.confidence == 1.0
    record_low = validate_record(_well_formed_record(confidence=-0.3))
    assert record_low is not None
    assert record_low.confidence == 0.0


def test_validate_returns_none_for_non_dict_input() -> None:
    assert validate_record("not a record") is None
    assert validate_record(None) is None
    assert validate_record([1, 2, 3]) is None


def test_filter_actionable_drops_low_confidence_records() -> None:
    """Records below the floor get dropped from the probability chain.
    A real LLM provider may hallucinate; this is the safety valve."""
    high = validate_record(_well_formed_record(confidence=0.85))
    border = validate_record(_well_formed_record(confidence=DEFAULT_CONFIDENCE_FLOOR))
    low = validate_record(_well_formed_record(confidence=0.5))
    assert high is not None and border is not None and low is not None
    actionable = filter_actionable([high, border, low])
    assert high in actionable
    assert border in actionable
    assert low not in actionable


def test_heuristic_extractor_implements_the_protocol() -> None:
    """The fallback honors the contract: same call shape, returns a list
    of `ExtractedAvailability` (empty when no LLM is wired in)."""
    extractor = HeuristicNarrativeExtractor()
    out = extractor.extract(
        home_team="Local FC",
        away_team="Visitor FC",
        source_text="Pedro Lopez is out with an injury.",
        source_url="https://example.com",
        source_title="Article",
    )
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, ExtractedAvailability)
