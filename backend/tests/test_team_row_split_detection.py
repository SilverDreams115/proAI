"""Detection of club rows that two ingestion sources split in two.

Every negative case below is a real false positive this detector
produced against production data before the rule that rejects it
existed. They are the point of the suite: a wrong "these are the same
club" invites a merge that destroys history, so the rules have to stay
strict as they are extended.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel, MatchModel, TeamModel
from app.services.team_row_split_detection import (
    RICH_HISTORY_MIN,
    find_split_candidates,
    names_look_split,
)


def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'split_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _team(session: Session, name: str) -> TeamModel:
    team = TeamModel(name=name, country="MX")
    session.add(team)
    session.flush()
    return team


def _competition(session: Session, name: str) -> CompetitionModel:
    competition = CompetitionModel(name=name, country="MX", season="2026")
    session.add(competition)
    session.flush()
    return competition


def _play(session: Session, competition, team, count: int, offset: int = 0) -> None:
    """Give `team` `count` fixtures in `competition` against filler sides."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        rival = _team(session, f"Rival {team.name} {offset + index}")
        session.add(
            MatchModel(
                competition_id=competition.id,
                home_team_id=team.id,
                away_team_id=rival.id,
                kickoff_at=base + timedelta(days=offset + index),
            )
        )
    session.flush()


# ---------------------------------------------------------------------------
# Name rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Seattle", "Seattle Sounders"),
        ("Vancouver", "Vancouver Whitecaps"),
        ("Minnesota", "Minnesota United"),
        ("Manchester United", "Man United"),  # the pair that motivated this
        ("Malmo", "Malmo FF"),
    ],
)
def test_same_club_under_two_spellings(left: str, right: str) -> None:
    assert names_look_split(left, right) or names_look_split(right, left)


@pytest.mark.parametrize(
    ("left", "right", "why"),
    [
        ("Real Sociedad", "Real Madrid", "distinct clubs sharing a first word"),
        ("Braga", "Bragantino", "prefix that does not end on a word boundary"),
        ("FC Kobenhavn", "FC Juarez", "share only the FC prefix"),
        ("CA River Plate", "CA Mineiro", "share only the CA prefix"),
        ("American Samoa", "America", "unrelated"),
        ("Las Vegas Lights", "Las Palmas", "unrelated"),
        ("Botafogo-SP", "Botafogo", "genuinely different clubs"),
    ],
)
def test_rejects_names_that_only_look_alike(left: str, right: str, why: str) -> None:
    assert not names_look_split(left, right), why
    assert not names_look_split(right, left), why


@pytest.mark.parametrize(
    ("womens", "mens"),
    [
        ("Barcelona Femenino", "Barcelona"),
        ("Tigres Femenil", "Tigres"),
        ("Chelsea Women", "Chelsea"),
    ],
)
def test_never_folds_a_womens_row_into_a_mens_row(womens: str, mens: str) -> None:
    """v23-v26 separated these deliberately; re-merging would undo it."""
    assert not names_look_split(womens, mens)
    assert not names_look_split(mens, womens)


def test_whitecaps_is_not_read_as_a_womens_side() -> None:
    """Matching " w" as a substring made every club whose second word
    starts with a W look like a women's team, which silently hid
    Vancouver from detection."""
    assert names_look_split("Vancouver", "Vancouver Whitecaps")


# ---------------------------------------------------------------------------
# Detection against the database
# ---------------------------------------------------------------------------

def test_finds_a_split_row_sharing_a_competition(db: Session) -> None:
    mls = _competition(db, "MLS")
    thin = _team(db, "Seattle")
    rich = _team(db, "Seattle Sounders")
    _play(db, mls, thin, 2)
    _play(db, mls, rich, RICH_HISTORY_MIN + 5, offset=100)
    db.commit()

    found = find_split_candidates(db, [thin.id, rich.id])
    assert thin.id in found
    candidate = found[thin.id]
    assert candidate.rich_team_name == "Seattle Sounders"
    assert candidate.thin_match_count == 2
    assert candidate.shared_competition == "MLS"
    # The rich row is not itself reported as thin.
    assert rich.id not in found


def test_ignores_similar_names_in_different_competitions(db: Session) -> None:
    """The constraint that killed the false positives: without it, name
    similarity alone proposed merges across unrelated leagues."""
    liga = _competition(db, "Liga MX")
    mls = _competition(db, "MLS")
    thin = _team(db, "Seattle")
    rich = _team(db, "Seattle Sounders")
    _play(db, liga, thin, 2)
    _play(db, mls, rich, RICH_HISTORY_MIN + 5, offset=100)
    db.commit()

    assert find_split_candidates(db, [thin.id, rich.id]) == {}


def test_ignores_a_club_with_no_richer_twin(db: Session) -> None:
    """Aberdeen: genuinely thin because no source ingests its league.
    Not repairable, so it must not be reported as if it were."""
    cup = _competition(db, "Scottish Cup")
    aberdeen = _team(db, "Aberdeen")
    hearts = _team(db, "Hearts")
    _play(db, cup, aberdeen, 1)
    _play(db, cup, hearts, 1, offset=50)
    db.commit()

    assert find_split_candidates(db, [aberdeen.id, hearts.id]) == {}


def test_ignores_a_twin_that_is_also_thin(db: Session) -> None:
    """Both rows thin means merging recovers nothing worth flagging."""
    mls = _competition(db, "MLS")
    thin = _team(db, "Seattle")
    other = _team(db, "Seattle Sounders")
    _play(db, mls, thin, 2)
    _play(db, mls, other, 5, offset=100)
    db.commit()

    assert find_split_candidates(db, [thin.id, other.id]) == {}


def test_prefers_the_richest_twin(db: Session) -> None:
    mls = _competition(db, "MLS")
    thin = _team(db, "Seattle")
    small = _team(db, "Seattle Reign")
    big = _team(db, "Seattle Sounders")
    _play(db, mls, thin, 2)
    _play(db, mls, small, RICH_HISTORY_MIN + 1, offset=100)
    _play(db, mls, big, RICH_HISTORY_MIN + 40, offset=300)
    db.commit()

    found = find_split_candidates(db, [thin.id])
    assert found[thin.id].rich_team_name == "Seattle Sounders"


def test_empty_input_is_a_no_op(db: Session) -> None:
    assert find_split_candidates(db, []) == {}
