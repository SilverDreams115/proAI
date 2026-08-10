"""Promotion must reuse an ingested fixture instead of minting a twin.

The exact-kickoff lookup is the unique constraint's notion of identity, and
promotion created a row whenever it missed. The LN programme and the feeds
routinely state one fixture an hour apart, so that miss produced a second row
for a match already in the database — and whatever arrived afterwards split
between the two. PGM-797 carries five such positions: evidence on the row the
slate points at, results on its twin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "twins.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_ingested_fixture(session, *, kickoff, is_placeholder=False):
    from app.models.tables import CompetitionModel, MatchModel, TeamModel

    comp = CompetitionModel(name="Copa Libertadores", country="World", season="2026")
    home = TeamModel(name="Bolivar", country="Bolivia")
    away = TeamModel(name="CS Independiente Rivadavia", country="Argentina")
    session.add_all([comp, home, away])
    session.flush()
    match = MatchModel(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff,
        is_placeholder=is_placeholder,
    )
    session.add(match)
    session.flush()
    return comp, home, away, match


def _promote(session, comp, home, away, *, kickoff):
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate

    payload = ProgolSlateCreate(
        label="Progol 797",
        draw_code="PGM-797",
        week_type="midweek",
        matches=[
            MatchReferencePayload(
                position=1,
                competition=CompetitionPayload(
                    name=comp.name, country=comp.country, season=comp.season
                ),
                home_team=TeamPayload(name=home.name, country=home.country),
                away_team=TeamPayload(name=away.name, country=away.country),
                kickoff_at=kickoff,
            )
        ],
    )
    return SlateRepository(session).upsert_slate(payload)


def _match_count(session):
    from app.models.tables import MatchModel
    from sqlalchemy import select, func

    return session.scalar(select(func.count()).select_from(MatchModel))


def test_promotion_reuses_a_fixture_stated_an_hour_apart(tmp_path):
    session = _make_session(tmp_path)
    ingested_kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)
    comp, home, away, ingested = _seed_ingested_fixture(session, kickoff=ingested_kickoff)

    # The LN programme states this fixture 30 minutes later.
    slate = _promote(session, comp, home, away, kickoff=ingested_kickoff + timedelta(minutes=30))

    assert _match_count(session) == 1
    assert slate.matches[0].match_id == ingested.id


def test_promotion_still_creates_when_nothing_is_near(tmp_path):
    """A fixture we have never ingested must still produce a row."""
    session = _make_session(tmp_path)
    ingested_kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)
    comp, home, away, ingested = _seed_ingested_fixture(session, kickoff=ingested_kickoff)

    slate = _promote(session, comp, home, away, kickoff=ingested_kickoff + timedelta(days=10))

    assert _match_count(session) == 2
    assert slate.matches[0].match_id != ingested.id


def test_a_real_row_wins_over_a_fabricated_one(tmp_path):
    """A previous slate's construction must never shadow an ingested fixture."""
    from app.models.tables import MatchModel

    session = _make_session(tmp_path)
    kickoff = datetime(2026, 5, 28, 0, 30, tzinfo=timezone.utc)
    comp, home, away, real = _seed_ingested_fixture(session, kickoff=kickoff)
    fabricated = MatchModel(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff + timedelta(minutes=10),
        is_placeholder=True,
    )
    session.add(fabricated)
    session.flush()

    # Closer in time to the fabricated row, but the real one must win.
    slate = _promote(session, comp, home, away, kickoff=kickoff + timedelta(minutes=12))

    assert slate.matches[0].match_id == real.id
