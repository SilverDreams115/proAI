"""One real match reported twice must count once in a form window.

Two feeds disagree about a fixture's kickoff — one stores local time against
the other's UTC, or dates a late kickoff by the calendar day it ends on — and
the fixture-identity constraint never catches it because the timestamps differ.
Both rows then carried a result and the match was counted twice.

What must NOT collapse matters as much: the same pair meets again in the same
competition, and a second leg or next season's fixture is a different match
even when the score repeats.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "dupes.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed(session):
    from app.models.tables import CompetitionModel, SourceModel, TeamModel

    comp = CompetitionModel(name="Brasileirao", country="Brazil", season="2026")
    home = TeamModel(name="Cruzeiro", country="Brazil")
    away = TeamModel(name="Coritiba", country="Brazil")
    source = SourceModel(
        name="src", base_url="http://x", kind="thesportsdb_season",
        parser_profile="p", is_active=True,
    )
    session.add_all([comp, home, away, source])
    session.flush()
    return comp, home, away, source


def _add_match(session, comp, home, away, source, *, kickoff, score):
    from app.models.tables import MatchModel, MatchResultModel

    match = MatchModel(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff,
    )
    session.add(match)
    session.flush()
    home_goals, away_goals = score
    session.add(
        MatchResultModel(
            match_id=match.id,
            source_id=source.id,
            home_goals=home_goals,
            away_goals=away_goals,
            played_at=kickoff,
            result_code="1" if home_goals > away_goals else ("2" if home_goals < away_goals else "X"),
        )
    )
    session.flush()
    return match


def test_same_fixture_reported_at_two_kickoffs_counts_once(tmp_path):
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    base = datetime(2026, 7, 30, 22, 0, tzinfo=timezone.utc)

    # The production case: Coritiba vs Cruzeiro, 9.9h apart, same 0-1.
    _add_match(session, comp, home, away, source, kickoff=base, score=(0, 1))
    _add_match(session, comp, home, away, source,
               kickoff=base + timedelta(hours=9, minutes=54), score=(0, 1))

    results = ResultRepository(session).list_recent_team_results(
        home.id, before=base + timedelta(days=30)
    )
    assert len(results) == 1


def test_widest_observed_skew_still_collapses(tmp_path):
    """32h apart is real: México vs South Korea arrived dated 17/06 and 18/06."""
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    base = datetime(2026, 6, 17, 2, 0, tzinfo=timezone.utc)

    _add_match(session, comp, home, away, source, kickoff=base, score=(1, 0))
    _add_match(session, comp, home, away, source,
               kickoff=base + timedelta(hours=32), score=(1, 0))

    results = ResultRepository(session).list_recent_team_results(
        home.id, before=base + timedelta(days=30)
    )
    assert len(results) == 1


def test_rematch_with_the_same_score_still_counts_twice(tmp_path):
    """A later fixture between the same pair is a different match."""
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    base = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)

    _add_match(session, comp, home, away, source, kickoff=base, score=(2, 1))
    _add_match(session, comp, home, away, source,
               kickoff=base + timedelta(days=120), score=(2, 1))

    results = ResultRepository(session).list_recent_team_results(
        home.id, before=base + timedelta(days=365)
    )
    assert len(results) == 2


def test_different_scores_at_close_kickoffs_both_survive(tmp_path):
    """Disagreeing scores are a data conflict, not a duplicate to swallow."""
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    base = datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)

    _add_match(session, comp, home, away, source, kickoff=base, score=(1, 0))
    _add_match(session, comp, home, away, source,
               kickoff=base + timedelta(hours=2), score=(3, 2))

    results = ResultRepository(session).list_recent_team_results(
        home.id, before=base + timedelta(days=30)
    )
    assert len(results) == 2


def test_head_to_head_collapses_the_duplicate_too(tmp_path):
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    base = datetime(2026, 5, 1, 20, 0, tzinfo=timezone.utc)

    _add_match(session, comp, home, away, source, kickoff=base, score=(1, 1))
    _add_match(session, comp, home, away, source,
               kickoff=base + timedelta(hours=22), score=(1, 1))
    upcoming = _add_match(session, comp, home, away, source,
                          kickoff=base + timedelta(days=90), score=(0, 0))

    results = ResultRepository(session).list_head_to_head_results_for_match(upcoming.id)
    assert len(results) == 1
