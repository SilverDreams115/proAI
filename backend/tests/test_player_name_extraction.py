"""Tests for the player-name extraction regex (Fase 4.4 / Hallazgo A10).

The previous ASCII-only pattern silently skipped Spanish, Portuguese and
Italian names with accents — exactly the leagues proAI tracks. The new
pattern must keep working for ASCII names and start picking up the
accented and apostrophed cases.
"""
from __future__ import annotations

from app.services.narrative_interpretation_service import NarrativeInterpretationService


def _extract(text: str) -> str | None:
    return NarrativeInterpretationService(  # noqa: SLF001 - accessing private helper on purpose
        availability_repository=None, entity_repository=None
    )._extract_player_name(text)


def test_extracts_plain_ascii_name() -> None:
    """Baseline: previous behavior must still work."""
    assert _extract("Pedro Lopez is out with an injury.") == "Pedro Lopez"


def test_extracts_name_with_spanish_accents() -> None:
    """Álvarez and Núñez were silently dropped before — that was the bug."""
    assert _extract("Álvaro Núñez se pierde el partido por lesión.") == "Álvaro Núñez"


def test_extracts_name_with_portuguese_diacritics() -> None:
    """Brazilian rosters routinely carry tildes; we ingest Brasileirao."""
    assert _extract("João Pedro sofreu uma lesão muscular.") == "João Pedro"


def test_extracts_compound_name_with_de() -> None:
    """Compound surnames with connector particles (Spanish/Portuguese)."""
    assert _extract("Carlos de Almeida regresa al once.") == "Carlos de Almeida"


def test_extracts_full_name_with_apostrophe_in_surname() -> None:
    """Apostrophes appear inside surnames (D'Alessandro, O'Higgins) and
    must not break the boundary detection."""
    assert _extract("Pablo D'Alessandro vuelve al once.") == "Pablo D'Alessandro"


def test_returns_none_when_no_capitalized_pair() -> None:
    """A lowercase headline must not produce a spurious 'name'."""
    assert _extract("striker is out due to injury") is None
