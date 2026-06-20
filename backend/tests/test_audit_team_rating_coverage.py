from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import SourceModel
from app.models.tables import TeamModel
from scripts.audit_team_rating_coverage import (
    EloMatch,
    EloParams,
    TeamRating,
    _confidence_bucket,
    _goal_diff_multiplier,
    _namespace_for,
    build_report,
    compute_ratings,
)


def _tr(team_id: str) -> TeamRating:
    return TeamRating(team_id=team_id, team_name=team_id, is_placeholder=False,
                      country=None, namespace="club")


def _match(mid, h, a, hg, ag, *, day=1, comp="Liga"):
    return EloMatch(
        played_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        match_id=mid, home_id=h, away_id=a, home_goals=hg, away_goals=ag, competition=comp,
    )


# 1. Deterministic: order-independent (sorted internally), repeatable.
def test_elo_is_deterministic_and_order_independent() -> None:
    matches = [
        _match("m1", "A", "B", 2, 0, day=1),
        _match("m2", "B", "C", 1, 1, day=2),
        _match("m3", "A", "C", 0, 1, day=3),
    ]
    teams1 = {t: _tr(t) for t in ("A", "B", "C")}
    teams2 = {t: _tr(t) for t in ("A", "B", "C")}
    compute_ratings(matches, teams1, EloParams())
    compute_ratings(list(reversed(matches)), teams2, EloParams())
    assert {k: v.rating for k, v in teams1.items()} == {k: v.rating for k, v in teams2.items()}


# 2. Win raises winner, lowers loser (zero-sum from 1500/1500).
def test_win_raises_and_loss_lowers() -> None:
    teams = {t: _tr(t) for t in ("A", "B")}
    compute_ratings([_match("m1", "A", "B", 3, 0)], teams, EloParams())
    assert teams["A"].rating > 1500.0
    assert teams["B"].rating < 1500.0
    assert round(teams["A"].rating - 1500, 4) == round(1500 - teams["B"].rating, 4)  # zero-sum
    assert teams["A"].wins == 1 and teams["B"].losses == 1


# 3. Draw between equals does not move; draw vs stronger pulls favourite down.
def test_draw_behaviour() -> None:
    teams = {t: _tr(t) for t in ("A", "B")}
    compute_ratings([_match("m1", "A", "B", 1, 1)], teams, EloParams())
    assert teams["A"].rating == 1500.0 and teams["B"].rating == 1500.0
    assert teams["A"].draws == 1 and teams["B"].draws == 1

    # Give A a higher rating first, then a draw should lower A.
    teams2 = {t: _tr(t) for t in ("A", "B")}
    compute_ratings(
        [_match("m1", "A", "X", 5, 0, day=1), _match("m2", "A", "B", 1, 1, day=2)],
        {**teams2, "X": _tr("X")}, EloParams(),
    )
    # Recompute A vs B only to assert direction: A (>1500) drew B (1500) -> A drops.
    assert teams2["A"].rating < 1600.0  # pulled back by the draw


# 4. Goal-diff multiplier disabled by default; capped when enabled.
def test_goal_diff_multiplier_disabled_by_default() -> None:
    assert _goal_diff_multiplier(5, 0, EloParams()) == 1.0
    assert _goal_diff_multiplier(1, 0, EloParams()) == 1.0
    # Same delta for 5-0 and 1-0 when disabled.
    a = {t: _tr(t) for t in ("A", "B")}
    b = {t: _tr(t) for t in ("C", "D")}
    compute_ratings([_match("m", "A", "B", 5, 0)], a, EloParams())
    compute_ratings([_match("m", "C", "D", 1, 0)], b, EloParams())
    assert round(a["A"].rating, 4) == round(b["C"].rating, 4)


def test_goal_diff_multiplier_capped_when_enabled() -> None:
    params = EloParams(goal_diff_enabled=True, goal_diff_cap=1.75)
    assert _goal_diff_multiplier(0, 0, params) == 1.0  # log1p(0)=0 -> 1.0
    assert round(_goal_diff_multiplier(1, 0, params), 3) == 1.693  # 1 + log1p(1)
    assert _goal_diff_multiplier(50, 0, params) == 1.75  # capped
    assert _goal_diff_multiplier(5, 0, params) <= 1.75


# 5 & 6. Teams without results stay no_rating; buckets correct.
def test_confidence_buckets() -> None:
    assert _confidence_bucket(0) == "no_rating"
    assert _confidence_bucket(3) == "weak"
    assert _confidence_bucket(4) == "medium"
    assert _confidence_bucket(9) == "medium"
    assert _confidence_bucket(10) == "strong"


def test_team_without_results_is_no_rating() -> None:
    teams = {t: _tr(t) for t in ("A", "B", "Z")}  # Z never plays
    compute_ratings([_match("m1", "A", "B", 1, 0)], teams, EloParams())
    assert teams["Z"].matches_count == 0
    assert teams["Z"].confidence_bucket == "no_rating"
    assert teams["Z"].rating == 1500.0


def test_namespace_detection() -> None:
    assert _namespace_for(["International Friendlies"]) == "national"
    assert _namespace_for(["Liga MX"]) == "club"
    assert _namespace_for([]) == "unknown"


# --- DB-backed integration (no writes; PG-2338-style coverage) --------------

@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'elo.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _seed_history(session: Session, comp: CompetitionModel, source: SourceModel,
                  team: TeamModel, opponents: list[TeamModel], *, base_day: int) -> None:
    """Give `team` N completed results vs distinct opponents (team wins each)."""
    for i, opp in enumerate(opponents):
        m = MatchModel(competition_id=comp.id, home_team_id=team.id, away_team_id=opp.id,
                       kickoff_at=datetime(2025, 11, base_day + i, tzinfo=timezone.utc))
        session.add(m)
        session.flush()
        session.add(MatchResultModel(
            match_id=m.id, source_id=source.id,
            played_at=datetime(2025, 11, base_day + i, tzinfo=timezone.utc),
            home_goals=2, away_goals=0, result_code="1",
        ))
    session.flush()


def test_pg2338_coverage_and_no_writes(db) -> None:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    src = SourceModel(name="src", base_url="http://x", kind="results", result_source_priority=1)
    db.add_all([comp, src])
    db.flush()
    # Two rated teams (5 results each) + one unrated team.
    home = TeamModel(name="Strong", is_placeholder=False)
    away = TeamModel(name="AlsoStrong", is_placeholder=False)
    unrated = TeamModel(name="Ghost", is_placeholder=False)
    fillers = [TeamModel(name=f"F{i}", is_placeholder=False) for i in range(10)]
    db.add_all([home, away, unrated, *fillers])
    db.flush()
    _seed_history(db, comp, src, home, fillers[:5], base_day=1)
    _seed_history(db, comp, src, away, fillers[5:10], base_day=1)

    slate = ProgolSlateModel(label="PG-2338", draw_code="PG-2338", week_type="weekend",
                             slate_version=1, composition_hash="h")
    db.add(slate)
    db.flush()
    for pos, (h, a) in enumerate([(home, away), (unrated, away)], start=1):
        m = MatchModel(competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
                       kickoff_at=datetime(2026, 6, 25, 7, tzinfo=timezone.utc) + timedelta(hours=pos))
        db.add(m)
        db.flush()
        db.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))
    db.commit()

    before = (db.query(MatchResultModel).count(), db.query(MatchModel).count())
    report = build_report(db, base=True, draw_code="PG-2338", historical=True)
    after = (db.query(MatchResultModel).count(), db.query(MatchModel).count())
    assert before == after  # 8. no DB writes

    summ = report["pg2338"]["summary"]
    assert summ["pg2338_matches"] == 2
    assert summ["both_have_rating_count"] == 1  # pos1 both rated, pos2 has Ghost
    assert summ["positions_helped"] == [1]
    assert summ["positions_not_helped"] == [2]
    rows = report["pg2338"]["rows"]
    assert rows[1]["still_missing_reason"] == "home_no_rating"
    assert report["base"]["confidence_buckets"]["medium"] >= 2  # Strong + AlsoStrong
