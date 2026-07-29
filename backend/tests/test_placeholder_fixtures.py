"""Fabricated-fixture formula, ladder detection and cierre rebase."""
from datetime import datetime, timedelta, timezone

from app.services.placeholder_fixtures import (
    fallback_kickoff,
    is_synthetic_ladder,
    ladder_base,
    rebase_to_cierre,
)


CIERRE = datetime(2026, 7, 28, 22, 55, tzinfo=timezone.utc)


def _ladder(cierre, count):
    return [(position, fallback_kickoff(cierre, position)) for position in range(1, count + 1)]


def test_fallback_kickoff_is_cierre_plus_twelve_hours_then_one_per_position():
    assert fallback_kickoff(CIERRE, 1) == CIERRE + timedelta(hours=12)
    assert fallback_kickoff(CIERRE, 9) == CIERRE + timedelta(hours=20)


def test_position_zero_and_below_clamp_to_the_base():
    assert fallback_kickoff(CIERRE, 0) == fallback_kickoff(CIERRE, 1)


def test_naive_cierre_is_treated_as_utc():
    assert fallback_kickoff(CIERRE.replace(tzinfo=None), 1) == fallback_kickoff(CIERRE, 1)


def test_ladder_base_recovers_the_cierre_the_slate_was_promoted_with():
    assert ladder_base(_ladder(CIERRE, 9)) == CIERRE + timedelta(hours=12)


def test_real_kickoffs_are_not_a_ladder():
    # Actual fixture times: irregular gaps, round minutes.
    entries = [
        (1, datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)),
        (2, datetime(2026, 8, 1, 17, 30, tzinfo=timezone.utc)),
        (3, datetime(2026, 8, 1, 17, 30, tzinfo=timezone.utc)),
        (4, datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)),
    ]
    assert ladder_base(entries) is None
    assert is_synthetic_ladder(entries) is False


def test_two_positions_an_hour_apart_are_not_enough_to_call_it_fabricated():
    # An ordinary back-to-back broadcast slot must not be mistaken for the
    # fabricator's signature.
    assert ladder_base(_ladder(CIERRE, 2)) is None


def test_one_real_kickoff_among_fabricated_ones_breaks_detection():
    entries = _ladder(CIERRE, 9)
    entries[4] = (5, datetime(2026, 7, 29, 1, 15, tzinfo=timezone.utc))
    assert ladder_base(entries) is None


def test_rebase_moves_every_fabricated_kickoff_onto_the_new_cierre():
    old_cierre = datetime(2026, 7, 29, 23, 37, 37, 574632, tzinfo=timezone.utc)
    entries = _ladder(old_cierre, 9)

    moved = rebase_to_cierre(CIERRE, entries)

    assert len(moved) == 9
    assert moved[1] == CIERRE + timedelta(hours=12)
    assert moved[9] == CIERRE + timedelta(hours=20)


def test_rebase_is_idempotent():
    entries = _ladder(CIERRE, 9)
    assert rebase_to_cierre(CIERRE, entries) == {}


def test_rebase_never_touches_real_kickoffs():
    entries = [
        (1, datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)),
        (2, datetime(2026, 8, 1, 17, 30, tzinfo=timezone.utc)),
        (3, datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)),
    ]
    assert rebase_to_cierre(CIERRE, entries) == {}


def test_pgm806_kickoffs_land_after_their_own_cierre_once_rebased():
    # The real incident: promoted on a provisional cierre (first_seen + the
    # 5-day MS PDF window), then corrected to 22:55 without moving the
    # kickoffs, which stranded all 9 a day and a half after the slate closed.
    provisional = datetime(2026, 7, 29, 23, 37, 37, 574632, tzinfo=timezone.utc)
    stored = _ladder(provisional, 9)
    assert all(kick > CIERRE + timedelta(days=1) for _, kick in stored)

    moved = rebase_to_cierre(CIERRE, stored)

    assert all(CIERRE < kick < CIERRE + timedelta(days=1) for kick in moved.values())


def _slate_payload(*, draw_code, kickoffs, is_placeholder):
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate

    return ProgolSlateCreate(
        label=f"Test {draw_code}",
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=CIERRE,
        matches=[
            MatchReferencePayload(
                position=position,
                competition=CompetitionPayload(name="Liga MX"),
                home_team=TeamPayload(name=f"H{position}"),
                away_team=TeamPayload(name=f"A{position}"),
                kickoff_at=kickoff,
                is_placeholder=is_placeholder,
            )
            for position, kickoff in kickoffs
        ],
    )


def _repo_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'placeholder_upsert.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _stored_marks(session, slate_id):
    """Read the marks back from the DB.

    `upsert_slate` deletes and re-creates the position links inside the same
    session, so the returned object's `matches` collection still carries the
    superseded ones until the session is expired.
    """
    from app.models.tables import ProgolSlateMatchModel

    session.expire_all()
    links = (
        session.query(ProgolSlateMatchModel)
        .filter(ProgolSlateMatchModel.slate_id == slate_id)
        .order_by(ProgolSlateMatchModel.position)
        .all()
    )
    return [link.match.is_placeholder for link in links]


def test_a_payload_with_no_opinion_never_clears_the_placeholder_mark(tmp_path):
    """The PG-2344 regression.

    current.json is exported from the DB and re-imported by the hourly refresh
    job. It did not model the fixture-level flag, so every re-import arrived
    with `is_placeholder` unset — and an unset value read as "a feed confirmed
    this fixture", wiping all 14 fabricated marks on the active slate. Absence
    of an opinion must leave the stored mark exactly as it was.
    """
    from app.repositories.slate_repository import SlateRepository

    session = _repo_session(tmp_path)
    try:
        repo = SlateRepository(session)
        kickoffs = [(p, fallback_kickoff(CIERRE, p)) for p in range(1, 5)]

        repo.upsert_slate(_slate_payload(draw_code="PG-2344", kickoffs=kickoffs, is_placeholder=True))
        session.flush()

        # The re-import: same fixtures, no opinion on the flag.
        slate = repo.upsert_slate(
            _slate_payload(draw_code="PG-2344", kickoffs=kickoffs, is_placeholder=None)
        )
        session.flush()

        assert _stored_marks(session, slate.id) == [True] * 4
    finally:
        session.close()


def test_an_explicit_real_fixture_still_clears_the_mark(tmp_path):
    """The guard must not freeze the flag: once a caller positively knows a
    feed reported the fixture, the construction is superseded."""
    from app.repositories.slate_repository import SlateRepository

    session = _repo_session(tmp_path)
    try:
        repo = SlateRepository(session)
        kickoffs = [(p, fallback_kickoff(CIERRE, p)) for p in range(1, 5)]

        repo.upsert_slate(_slate_payload(draw_code="PG-2345", kickoffs=kickoffs, is_placeholder=True))
        session.flush()

        slate = repo.upsert_slate(
            _slate_payload(draw_code="PG-2345", kickoffs=kickoffs, is_placeholder=False)
        )
        session.flush()

        assert _stored_marks(session, slate.id) == [False] * 4
    finally:
        session.close()


def test_a_fabricated_payload_never_marks_a_row_a_feed_confirmed(tmp_path):
    """One-way rule: a construction cannot downgrade an observed fixture."""
    from app.repositories.slate_repository import SlateRepository

    session = _repo_session(tmp_path)
    try:
        repo = SlateRepository(session)
        kickoffs = [(p, fallback_kickoff(CIERRE, p)) for p in range(1, 5)]

        repo.upsert_slate(_slate_payload(draw_code="PG-2346", kickoffs=kickoffs, is_placeholder=False))
        session.flush()

        slate = repo.upsert_slate(
            _slate_payload(draw_code="PG-2346", kickoffs=kickoffs, is_placeholder=True)
        )
        session.flush()

        assert _stored_marks(session, slate.id) == [False] * 4
    finally:
        session.close()
