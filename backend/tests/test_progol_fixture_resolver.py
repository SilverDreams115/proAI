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


def _seed_history(session, *, home_name: str, away_name: str, competition_name: str, kickoff_at,
                  is_placeholder: bool = False):
    """Like `_seed_match`, but reuses the teams and competition already in
    the session so a club can build up history across several tournaments."""
    from app.models.tables import CompetitionModel, MatchModel, TeamModel
    from sqlalchemy import select

    def _get_or_create(model, name):
        row = session.execute(select(model).where(model.name == name)).scalars().first()
        if row is None:
            row = model(name=name)
            session.add(row)
            session.flush()
        return row

    competition = _get_or_create(CompetitionModel, competition_name)
    home = _get_or_create(TeamModel, home_name)
    away = _get_or_create(TeamModel, away_name)
    match = MatchModel(
        competition=competition,
        home_team=home,
        away_team=away,
        kickoff_at=kickoff_at,
        is_placeholder=is_placeholder,
    )
    session.add(match)
    session.flush()
    return match


def test_infer_competition_prefers_the_one_both_teams_play(tmp_path) -> None:
    """The two clubs never met, but both have history in a continental cup
    while each also plays its own domestic league. The cup is the only
    competition that can hold this fixture, so it must win over either
    league — the single-team fallback would have picked one at random."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        base = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        # Rivadavia: plenty of domestic football, a little continental.
        for i in range(6):
            _seed_history(
                session, home_name="RIVADAVIA", away_name="RIVAL_ARG",
                competition_name="Argentinian Primera Division",
                kickoff_at=base - timedelta(days=30 * (i + 1)),
            )
        _seed_history(
            session, home_name="RIVADAVIA", away_name="OTRO_SUDAMERICANO",
            competition_name="Copa Libertadores", kickoff_at=base - timedelta(days=200),
        )
        # Fluminense: same shape, different league.
        for i in range(6):
            _seed_history(
                session, home_name="FLUMINENSE", away_name="RIVAL_BRA",
                competition_name="Brasileirao",
                kickoff_at=base - timedelta(days=30 * (i + 1)),
            )
        _seed_history(
            session, home_name="FLUMINENSE", away_name="OTRO_SUDAMERICANO",
            competition_name="Copa Libertadores", kickoff_at=base - timedelta(days=210),
        )

        resolver = ProgolFixtureResolver(session)
        inferred = resolver.infer_competition_for_pair("RIVADAVIA", "FLUMINENSE")
        assert inferred is not None
        assert inferred.name == "Copa Libertadores"
    finally:
        session.close()


def test_infer_competition_returns_none_when_known_pair_shares_nothing(tmp_path) -> None:
    """PGM-809 position 1: Fenerbahce vs Lyon. Both clubs are known, they
    never met, and they share no competition — the old fallback handed the
    fixture Lyon's Ligue 1. Now nobody's league is admissible and the
    caller falls back to the synthetic placeholder competition."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        base = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        for i in range(5):
            _seed_history(
                session, home_name="FENERBAHCE", away_name="RIVAL_TUR",
                competition_name="T1", kickoff_at=base - timedelta(days=30 * (i + 1)),
            )
            _seed_history(
                session, home_name="LYON", away_name="RIVAL_FRA",
                competition_name="F1", kickoff_at=base - timedelta(days=30 * (i + 1)),
            )

        resolver = ProgolFixtureResolver(session)
        assert resolver.infer_competition_for_pair("FENERBAHCE", "LYON") is None
    finally:
        session.close()


def test_infer_competition_ignores_placeholder_history(tmp_path) -> None:
    """A placeholder row carries a competition that was itself inferred.
    Counting it would let a previous bad guess confirm itself: the
    Brasileirao placeholder PGM-809 created for Rivadavia must not make
    Brasileirao a competition "both teams play"."""
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        base = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        for i in range(5):
            _seed_history(
                session, home_name="RIVADAVIA", away_name="RIVAL_ARG",
                competition_name="Argentinian Primera Division",
                kickoff_at=base - timedelta(days=30 * (i + 1)),
            )
            _seed_history(
                session, home_name="FLUMINENSE", away_name="RIVAL_BRA",
                competition_name="Brasileirao",
                kickoff_at=base - timedelta(days=30 * (i + 1)),
            )
        # The bad guess from the previous promotion, still on the books.
        _seed_history(
            session, home_name="RIVADAVIA", away_name="FLUMINENSE",
            competition_name="Brasileirao", kickoff_at=base + timedelta(days=5),
            is_placeholder=True,
        )

        resolver = ProgolFixtureResolver(session)
        assert resolver.infer_competition_for_pair("RIVADAVIA", "FLUMINENSE") is None
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


def test_resolver_never_adopts_a_fabricated_fixture(tmp_path) -> None:
    """A previous slate's placeholder row must not resolve as a real match.

    When no feed reports a pair, promotion fabricates a fixture whose kickoff
    is derived from THAT slate's cierre. Left visible to the resolver, the
    next slate for the same pair finds it, reports "real match found" and
    copies the invented kickoff forward — which is how 16 match rows ended up
    shared between two slates in production. The pair must keep falling
    through to the fallback, which at least marks what it builds.
    """
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        fabricated = _seed_match(
            session,
            home_name="CORINTHIANS",
            away_name="PARANAENSE",
            competition_name="Brasileirao",
            kickoff_at=cierre + timedelta(hours=12),
        )
        fabricated.is_placeholder = True
        session.flush()

        resolver = ProgolFixtureResolver(session)
        assert resolver.resolve_pair("CORINTHIANS", "PARANAENSE", cierre) is None
    finally:
        session.close()


def test_a_real_fixture_still_resolves_for_a_pair_that_also_has_a_placeholder(tmp_path) -> None:
    """The guard filters placeholders, it does not blind the resolver: once a
    feed reports the fixture, that row is what the next slate must adopt."""
    from app.models.tables import CompetitionModel, MatchModel
    from app.services.progol_fixture_resolver import ProgolFixtureResolver

    session = _make_session(tmp_path)
    try:
        cierre = datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)
        fabricated = _seed_match(
            session,
            home_name="CORINTHIANS",
            away_name="PARANAENSE",
            competition_name="Brasileirao",
            kickoff_at=cierre + timedelta(hours=12),
        )
        fabricated.is_placeholder = True
        # Same team rows as the fabricated fixture — a second _seed_match would
        # create duplicate teams and the resolver would look up only one set.
        real_competition = CompetitionModel(
            name="Brasileirao Serie A", country="Brazil", season="2026"
        )
        session.add(real_competition)
        session.flush()
        real = MatchModel(
            competition=real_competition,
            home_team=fabricated.home_team,
            away_team=fabricated.away_team,
            kickoff_at=cierre + timedelta(hours=30),
            venue="Neo Quimica Arena",
        )
        session.add(real)
        session.flush()

        resolver = ProgolFixtureResolver(session)
        resolved = resolver.resolve_pair("CORINTHIANS", "PARANAENSE", cierre)
        assert resolved is not None
        assert resolved.id == real.id
    finally:
        session.close()
