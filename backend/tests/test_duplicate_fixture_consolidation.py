"""v37 folds a fixture recorded twice into one row that keeps everything.

The migration exists because `uq_matches_fixture_identity` keys on the exact
kickoff, so two feeds an hour apart produce two rows and whatever arrives later
splits between them. PGM-797 is the live case: five positions on rows holding
the evidence while their twins hold the results.

The slate-link repoint has its own test because a dry run without it stripped
PGM-800 from complete coverage to 4/9 — the results moved to the survivor while
the slate kept pointing at the emptied row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "consolidate.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _fixture(session, comp, home, away, kickoff, *, is_placeholder=False):
    from app.models.tables import MatchModel

    match = MatchModel(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff,
        is_placeholder=is_placeholder,
    )
    session.add(match)
    session.flush()
    return match


def _seed(session):
    from app.models.tables import CompetitionModel, SourceModel, TeamModel

    comp = CompetitionModel(name="Copa Libertadores", country="World", season="2026")
    home = TeamModel(name="Bolivar", country="Bolivia")
    away = TeamModel(name="Rivadavia", country="Argentina")
    source = SourceModel(
        name="src", base_url="http://x", kind="thesportsdb_season",
        parser_profile="p", is_active=True,
    )
    session.add_all([comp, home, away, source])
    session.flush()
    return comp, home, away, source


def _add_result(session, match, source, kickoff, score=(1, 3)):
    from app.models.tables import MatchResultModel

    session.add(
        MatchResultModel(
            match_id=match.id,
            source_id=source.id,
            home_goals=score[0],
            away_goals=score[1],
            played_at=kickoff,
            result_code="2",
        )
    )
    session.flush()


def _link_slate(session, match, *, draw_code="PGM-797"):
    from app.models.tables import ProgolSlateModel, ProgolSlateMatchModel

    slate = ProgolSlateModel(
        label=draw_code, draw_code=draw_code, week_type="midweek", is_archived=False
    )
    session.add(slate)
    session.flush()
    link = ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=1)
    session.add(link)
    session.flush()
    return slate, link


def _run(session):
    from app.db.migrations import _migrate_to_v37

    session.commit()
    _migrate_to_v37(session.connection())
    session.commit()


def test_result_moves_onto_the_row_the_slate_points_at(tmp_path):
    """The PGM-797 shape: slate + evidence on one row, result on its twin."""
    from app.models.tables import MatchResultModel
    from sqlalchemy import select

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)

    linked = _fixture(session, comp, home, away, kickoff + timedelta(minutes=30))
    twin = _fixture(session, comp, home, away, kickoff)
    _add_result(session, twin, source, kickoff)
    _, link = _link_slate(session, linked)

    _run(session)

    session.refresh(link)
    assert link.match_id == linked.id, "the slate must keep its own row"
    moved = session.scalars(
        select(MatchResultModel).where(MatchResultModel.match_id == linked.id)
    ).all()
    assert len(moved) == 1, "the result must land on the slate's row"


def test_the_emptied_twin_is_taken_out_of_circulation(tmp_path):
    from app.models.tables import MatchModel

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)

    linked = _fixture(session, comp, home, away, kickoff + timedelta(minutes=30))
    twin = _fixture(session, comp, home, away, kickoff)
    _add_result(session, twin, source, kickoff)
    _link_slate(session, linked)

    _run(session)

    assert session.get(MatchModel, twin.id) is not None, "nothing may be deleted"
    assert session.get(MatchModel, twin.id).is_placeholder is True


def test_a_slate_on_the_other_row_keeps_its_coverage(tmp_path):
    """The regression a dry run caught: two slates, one per row of a cluster.

    Without repointing progol_slate_matches the second slate was left pointing
    at the row the results had just been moved off.
    """
    from app.models.tables import MatchResultModel
    from sqlalchemy import select

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)

    first = _fixture(session, comp, home, away, kickoff)
    second = _fixture(session, comp, home, away, kickoff + timedelta(hours=1))
    _add_result(session, first, source, kickoff)
    _, link_a = _link_slate(session, first, draw_code="PGM-799")
    _, link_b = _link_slate(session, second, draw_code="PGM-800")

    _run(session)

    session.refresh(link_a)
    session.refresh(link_b)
    assert link_a.match_id == link_b.match_id, "both slates must land on one row"
    survivor = link_a.match_id
    results = session.scalars(
        select(MatchResultModel).where(MatchResultModel.match_id == survivor)
    ).all()
    assert len(results) == 1, "neither slate may lose its result"


def test_distant_fixtures_between_the_same_pair_are_left_alone(tmp_path):
    from app.models.tables import MatchModel

    session = _make_session(tmp_path)
    comp, home, away, source = _seed(session)
    kickoff = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)

    first = _fixture(session, comp, home, away, kickoff)
    second_leg = _fixture(session, comp, home, away, kickoff + timedelta(days=120))
    _add_result(session, first, source, kickoff)
    _add_result(session, second_leg, source, kickoff + timedelta(days=120))

    _run(session)

    assert session.get(MatchModel, first.id).is_placeholder is False
    assert session.get(MatchModel, second_leg.id).is_placeholder is False
