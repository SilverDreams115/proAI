"""Fase 3.1 — Unit tests for ProgolFixtureResolver.

The resolver is what turns a "MÉXICO vs AUSTRALIA" string from the LN
PDF into a real upcoming match in the DB. These tests cover the three
outcomes the promote pipeline depends on:

  * Both teams known + match in window → resolve_pair returns the match
  * One or both teams unknown → returns None (caller falls back to
    placeholder, doesn't blow up)
  * Teams known but no match in window → returns None
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "resolver.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_match(session, *, home_name: str, away_name: str, competition_name: str, kickoff_at):
    from app.models.tables import CompetitionModel, MatchModel, TeamModel

    competition = CompetitionModel(name=competition_name, country="World", season="2026")
    home = TeamModel(name=home_name, country=None)
    away = TeamModel(name=away_name, country=None)
    session.add_all([competition, home, away])
    session.flush()
    match = MatchModel(
        competition=competition,
        home_team=home,
        away_team=away,
        kickoff_at=kickoff_at,
        venue="Estadio Test",
    )
    session.add(match)
    session.flush()
    return match


def test_resolver_returns_match_when_pair_and_window_align(tmp_path) -> None:
    """Happy path: a real upcoming match exists for the pair around the
    cierre. The resolver should return it with eagerly-loaded teams +
    competition (so callers can read those without extra queries)."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        kickoff = cierre + timedelta(hours=18)
        _seed_match(
            session,
            home_name="MÉXICO",
            away_name="AUSTRALIA",
            competition_name="Friendlies International",
            kickoff_at=kickoff,
        )

        resolver = ProgolFixtureResolver(session)
        match = resolver.resolve_pair("MÉXICO", "AUSTRALIA", cierre)
        assert match is not None
        # SQLite drops tzinfo; compare wall-clock UTC.
        kickoff_actual = match.kickoff_at
        if kickoff_actual.tzinfo is not None:
            kickoff_actual = kickoff_actual.replace(tzinfo=None)
        assert kickoff_actual == kickoff.replace(tzinfo=None)
        assert match.competition.name == "Friendlies International"
    finally:
        session.close()


def test_resolver_returns_none_when_team_unknown(tmp_path) -> None:
    """If either side doesn't resolve to an existing team, we don't try
    to invent one — return None so the promote step falls back to a
    placeholder fixture."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        # Only seed the home team. The away team won't resolve.
        from app.models.tables import TeamModel
        session.add(TeamModel(name="MÉXICO", country=None))
        session.flush()

        resolver = ProgolFixtureResolver(session)
        match = resolver.resolve_pair("MÉXICO", "AUSTRALIA", cierre)
        assert match is None
    finally:
        session.close()


def test_resolver_returns_none_when_match_outside_window(tmp_path) -> None:
    """Both teams resolve, but the only upcoming match for this pair is
    weeks after the cierre. Resolver must reject it — promoting against
    a far-future fixture would tie this slate to the wrong concurso."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        # 30 days after cierre — well outside the 96h window.
        kickoff = cierre + timedelta(days=30)
        _seed_match(
            session,
            home_name="MÉXICO",
            away_name="AUSTRALIA",
            competition_name="Friendlies International",
            kickoff_at=kickoff,
        )
        resolver = ProgolFixtureResolver(session)
        assert resolver.resolve_pair("MÉXICO", "AUSTRALIA", cierre) is None
    finally:
        session.close()


def test_infer_competition_uses_most_played_for_pair(tmp_path) -> None:
    """When the upcoming-match lookup fails but both teams exist with a
    shared history, infer_competition_for_pair should return the
    competition both teams played in most often. This keeps the
    readiness policy honest for placeholder fixtures instead of pinning
    them to "unclassified"."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        # Two Brasileirao meetings, one La Liga meeting — Brasileirao
        # should win the tie-break by count.
        _seed_match(
            session, home_name="GRÊMIO", away_name="CORINTHIANS",
            competition_name="Brasileirao", kickoff_at=cierre - timedelta(days=120),
        )
        _seed_match(
            session, home_name="CORINTHIANS", away_name="GRÊMIO",
            competition_name="Brasileirao", kickoff_at=cierre - timedelta(days=60),
        )
        _seed_match(
            session, home_name="GRÊMIO", away_name="CORINTHIANS",
            competition_name="La Liga", kickoff_at=cierre - timedelta(days=400),
        )

        resolver = ProgolFixtureResolver(session)
        inferred = resolver.infer_competition_for_pair("GRÊMIO", "CORINTHIANS")
        assert inferred is not None
        assert inferred.name == "Brasileirao"
    finally:
        session.close()


def test_infer_competition_falls_back_to_single_team(tmp_path) -> None:
    """If only one of the two teams is known, the resolver should still
    return that team's most-played competition so the placeholder match
    inherits a real readiness policy."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        _seed_match(
            session, home_name="TOLUCA", away_name="TIGRES",
            competition_name="Liga MX", kickoff_at=cierre - timedelta(days=200),
        )
        resolver = ProgolFixtureResolver(session)
        # AWAY team intentionally unknown.
        inferred = resolver.infer_competition_for_pair("TOLUCA", "EQUIPO_DESCONOCIDO")
        assert inferred is not None
        assert inferred.name == "Liga MX"
    finally:
        session.close()


def test_infer_competition_returns_none_when_both_teams_unknown(tmp_path) -> None:
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        resolver = ProgolFixtureResolver(session)
        assert resolver.infer_competition_for_pair("DESCONOCIDO_A", "DESCONOCIDO_B") is None
    finally:
        session.close()


def test_resolve_many_returns_only_matched_positions(tmp_path) -> None:
    """Bulk-resolve must return a position-keyed dict that only contains
    the pairs that actually matched — unmatched positions simply absent
    so the caller drives the placeholder fallback per position."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        kickoff = cierre + timedelta(hours=24)
        _seed_match(
            session,
            home_name="REAL MADRID",
            away_name="ATLÉTICO",
            competition_name="La Liga",
            kickoff_at=kickoff,
        )

        resolver = ProgolFixtureResolver(session)
        resolved = resolver.resolve_many(
            [
                (1, "MÉXICO", "AUSTRALIA"),  # no team → not resolved
                (4, "REAL MADRID", "ATLÉTICO"),  # matches
            ],
            cierre,
        )
        assert set(resolved.keys()) == {4}
        assert resolved[4].competition.name == "La Liga"
    finally:
        session.close()
