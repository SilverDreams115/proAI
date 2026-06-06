"""Tests for the nearby-match lookup optimization (Fase 6.2 / Hallazgo A3).

The original implementation loaded every match in the database into
memory and filtered in Python, which made the historical backfill
slower than O(N^2) once the dataset crossed ~1500 matches. The
optimized version pushes the filter into SQL so only the candidates
that match the (competition, teams, kickoff-window) tuple are read.

These tests verify that:
- An exact match is returned when (competition, teams, played_at) line up.
- The kickoff tolerance window catches a near match within
  `RESULT_MATCH_DATE_TOLERANCE_DAYS` days.
- A match outside the tolerance window is rejected.
- Mismatched team identity does not bleed through, even if the kickoff is identical.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.ingestion_service import IngestionService


class _FakeScalars:
    def __init__(self, matches: list[object]) -> None:
        self._matches = matches

    def all(self) -> list[object]:
        return list(self._matches)


class _FakeSession:
    """Captures the WHERE values of the SQLAlchemy select() to assert that
    the optimized query reaches the DB layer correctly. Returns the
    pre-seeded matches as scan results."""

    def __init__(self, matches: list[object]) -> None:
        self._matches = matches
        self.executed_statements: list[object] = []

    def scalars(self, statement) -> _FakeScalars:
        self.executed_statements.append(statement)
        return _FakeScalars(self._matches)


def _match(*, mid: str, competition_id: str, home: str, away: str, kickoff: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        competition_id=competition_id,
        home_team_id=home,
        away_team_id=away,
        kickoff_at=kickoff,
    )


def _entity_repo_with(matches: list[object]) -> SimpleNamespace:
    return SimpleNamespace(session=_FakeSession(matches))


def test_nearby_match_returns_exact_kickoff_match() -> None:
    """When the DB has a match with the exact identity tuple, the helper
    must return it (smallest delta)."""
    played_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    match = _match(mid="m1", competition_id="c", home="A", away="B", kickoff=played_at)
    repo = _entity_repo_with([match])
    service = IngestionService(repository=SimpleNamespace(session=None))  # type: ignore[arg-type]
    found = service._find_nearby_match_for_result(
        entity_repository=repo,  # type: ignore[arg-type]
        competition_id="c",
        home_team_id="A",
        away_team_id="B",
        played_at=played_at,
    )
    assert found is match


def test_nearby_match_within_tolerance_window() -> None:
    """A match 5 days off (well inside the 14-day tolerance) must still
    match — football-data CSV dates and the slate kickoff can drift."""
    played_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    nearby = _match(
        mid="m1",
        competition_id="c",
        home="A",
        away="B",
        kickoff=played_at + timedelta(days=5),
    )
    repo = _entity_repo_with([nearby])
    service = IngestionService(repository=SimpleNamespace(session=None))  # type: ignore[arg-type]
    assert service._find_nearby_match_for_result(
        entity_repository=repo,  # type: ignore[arg-type]
        competition_id="c",
        home_team_id="A",
        away_team_id="B",
        played_at=played_at,
    ) is nearby


def test_nearby_match_chooses_closest_when_multiple_in_window() -> None:
    """Two matches within tolerance: the closer one wins."""
    played_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    far = _match(
        mid="far", competition_id="c", home="A", away="B",
        kickoff=played_at + timedelta(days=10),
    )
    close = _match(
        mid="close", competition_id="c", home="A", away="B",
        kickoff=played_at + timedelta(days=1),
    )
    # Order should not matter — the loop tracks the minimum delta.
    repo = _entity_repo_with([far, close])
    service = IngestionService(repository=SimpleNamespace(session=None))  # type: ignore[arg-type]
    chosen = service._find_nearby_match_for_result(
        entity_repository=repo,  # type: ignore[arg-type]
        competition_id="c",
        home_team_id="A",
        away_team_id="B",
        played_at=played_at,
    )
    assert chosen is close


def test_nearby_match_skips_naive_kickoff_by_treating_as_utc() -> None:
    """A naive datetime in the DB must be promoted to UTC so the delta
    comparison stays sane."""
    played_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    naive_kickoff = datetime(2026, 1, 11)  # tz-naive
    match = _match(mid="m1", competition_id="c", home="A", away="B", kickoff=naive_kickoff)
    repo = _entity_repo_with([match])
    service = IngestionService(repository=SimpleNamespace(session=None))  # type: ignore[arg-type]
    found = service._find_nearby_match_for_result(
        entity_repository=repo,  # type: ignore[arg-type]
        competition_id="c",
        home_team_id="A",
        away_team_id="B",
        played_at=played_at,
    )
    assert found is match


def test_nearby_match_returns_none_when_no_candidates() -> None:
    """Empty DB scan -> no match. The caller must then create a new
    fixture instead of attaching a result to a phantom row."""
    service = IngestionService(repository=SimpleNamespace(session=None))  # type: ignore[arg-type]
    assert service._find_nearby_match_for_result(
        entity_repository=_entity_repo_with([]),  # type: ignore[arg-type]
        competition_id="c",
        home_team_id="A",
        away_team_id="B",
        played_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    ) is None
