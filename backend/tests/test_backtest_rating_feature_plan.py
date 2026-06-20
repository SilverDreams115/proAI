"""R4: rating-feature backtest PLANNING harness — read-only, no training.

Confirms: per-competition grouping, candidate detection (learning_ready +
coverage), thin competitions blocked, and that building the plan writes
nothing to the DB.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import SourceModel
from app.models.tables import TeamModel
from scripts import backtest_rating_feature_plan as harness


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'plan.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed(session):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    src = SourceModel(
        name="src", base_url="http://x", kind="thesportsdb_season",
        parser_profile="p", is_active=True, result_source_priority=10,
    )
    liga = CompetitionModel(name="Liga", country="X", season="2026")
    thin = CompetitionModel(name="Thinleague", country="Y", season="2026")
    teams = [TeamModel(name=f"T{i}", country=None) for i in range(4)]
    thin_teams = [TeamModel(name=f"S{i}", country=None) for i in range(2)]
    session.add_all([src, liga, thin, *teams, *thin_teams])
    session.flush()

    day = itertools.count(1)

    def _match_with_result(home, away, comp, hg, ag):
        d = next(day)
        kickoff = base + timedelta(days=d)
        m = MatchModel(
            competition_id=comp.id, home_team_id=home.id,
            away_team_id=away.id, kickoff_at=kickoff,
        )
        session.add(m)
        session.flush()
        code = "L" if hg > ag else ("E" if hg == ag else "V")
        session.add(MatchResultModel(
            match_id=m.id, source_id=src.id, played_at=kickoff,
            home_goals=hg, away_goals=ag, result_code=code,
        ))
        return m

    # Double round-robin in Liga: each of 4 teams plays 6 → medium+ (>=4).
    pairs = list(itertools.permutations(range(4), 2))
    for hi, ai in pairs:
        _match_with_result(teams[hi], teams[ai], liga, (hi + 1), ai)

    # Thinleague: a single match → not learning-ready under min_matches=2.
    _match_with_result(thin_teams[0], thin_teams[1], thin, 1, 0)
    session.commit()


def test_plan_groups_by_competition_and_detects_candidate(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)

    plan = harness.build_plan(session, min_matches=2)
    by_comp = {row["competition"]: row for row in plan}

    assert "Liga" in by_comp and "Thinleague" in by_comp

    liga = by_comp["Liga"]
    assert liga["matches"] == 12
    assert liga["results_complete"] == 12
    assert liga["learning_ready"] is True
    assert liga["rating_medium_plus_rate"] >= 0.8
    assert liga["candidate_for_backtest"] is True
    assert liga["blocker"] == ""

    thin = by_comp["Thinleague"]
    assert thin["learning_ready"] is False
    assert thin["candidate_for_backtest"] is False
    assert "insufficient_results" in thin["blocker"]
    session.close()


def test_build_plan_writes_nothing(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)

    before_results = session.query(MatchResultModel).count()
    harness.build_plan(session, min_matches=2)

    assert not session.new
    assert not session.dirty
    assert session.query(MatchResultModel).count() == before_results
    session.rollback()
    session.close()
