"""Knockout flag detection, and its survival across a slate re-upsert.

``upsert_slate`` rebuilds a slate's match rows from scratch on every
call. Before this suite existed it did so without carrying the
``is_knockout`` flags over, so a routine slate refresh silently reverted
the operator's knockout marking and with it the draw trim — a change to
real picks that produced no error and no log line.

The detection tests pin the conservative contract: only competitions
where *every* fixture is an elimination tie auto-flag, and anything the
name cannot settle abstains with ``None`` instead of guessing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import ProgolSlateMatchModel
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.knockout_detection import (
    detect_knockout,
    detect_knockout_from_stage,
    is_ambiguous_competition,
    resolve_knockout,
)


def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'knockout_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _payload(
    draw_code: str = "PG-KO-1",
    *,
    competition: str = "Liga MX",
    n: int = 14,
    away_prefix: str = "Away",
) -> ProgolSlateCreate:
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name=competition),
            home_team=TeamPayload(name=f"Home{i}"),
            away_team=TeamPayload(name=f"{away_prefix}{i}"),
            kickoff_at=now + timedelta(days=10),
        )
        for i in range(1, n + 1)
    ]
    return ProgolSlateCreate(
        label=f"Test {draw_code}",
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
        matches=matches,
    )


def _rule(session: Session, row: ProgolSlateMatchModel, is_knockout: bool) -> None:
    """Record an operator ruling the way the knockout endpoint does —
    the flag alone is not enough, the ruling has to be attributed."""
    row.is_knockout = is_knockout
    row.knockout_source = "operator"
    session.commit()


def _flags(session: Session, slate_id: str) -> dict[int, bool]:
    rows = session.scalars(
        select(ProgolSlateMatchModel).where(ProgolSlateMatchModel.slate_id == slate_id)
    ).all()
    return {row.position: bool(row.is_knockout) for row in rows}


# ---------------------------------------------------------------------------
# Detection contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["Copa de Alemania", "Russian Cup", "Copa de Rusia", "Copa Chile", "Copa del Rey"],
)
def test_domestic_cups_auto_flag(name: str) -> None:
    assert detect_knockout(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "UEFA Champions League",
        "Copa Libertadores",
        "Copa Sudamericana",
        "UEFA Conference League",
    ],
)
def test_group_stage_competitions_abstain(name: str) -> None:
    """These run a group stage under the same name as their knockout
    rounds, so the name cannot decide and must not guess."""
    assert detect_knockout(name) is None
    assert is_ambiguous_competition(name) is True


@pytest.mark.parametrize("name", ["Liga MX", "Brasileirao", "MLS", "E0", "J1 League"])
def test_plain_leagues_do_not_flag(name: str) -> None:
    assert detect_knockout(name) is False


def test_leagues_cup_abstains_despite_cup_token() -> None:
    """Leagues Cup opens with a league phase where draws are ordinary and
    only later switches to knockout rounds, under one name. The "cup"
    token used to auto-flag the whole league phase and trim the draw ~55%
    (it did exactly that to all 9 positions of PGM-807), so the name must
    abstain and leave the call to an operator."""
    assert detect_knockout("Leagues Cup") is None
    assert is_ambiguous_competition("Leagues Cup") is True


def test_efl_championship_is_not_treated_as_a_cup() -> None:
    """Second-tier league whose name contains 'Championship' — the
    token heuristic must not read it as a cup."""
    assert detect_knockout("EFL Championship") is False


def test_blank_competition_abstains() -> None:
    assert detect_knockout(None) is None
    assert detect_knockout("   ") is None


# ---------------------------------------------------------------------------
# Persistence across re-upsert — the regression this suite exists for
# ---------------------------------------------------------------------------

def test_operator_knockout_flag_survives_reupsert(db: Session) -> None:
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()

    # Operator marks position 3 as a liguilla final — Liga MX by name is
    # a plain league, so this can only come from a human.
    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 3,
        )
    )
    assert row is not None
    _rule(db, row, True)

    repo.upsert_slate(_payload())
    db.commit()

    flags = _flags(db, slate.id)
    assert flags[3] is True, "re-upsert discarded the operator's knockout flag"
    assert sum(flags.values()) == 1


def test_operator_clear_also_survives_reupsert(db: Session) -> None:
    """An explicit clear is a decision too: a cup position the operator
    turned off must not be re-flagged by auto-detection."""
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload(competition="Copa Chile"))
    db.commit()
    assert all(_flags(db, slate.id).values()), "cup slate should auto-flag"

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 5,
        )
    )
    assert row is not None
    _rule(db, row, False)

    repo.upsert_slate(_payload(competition="Copa Chile"))
    db.commit()

    assert _flags(db, slate.id)[5] is False, "auto-detection overrode an operator clear"


def test_flag_survives_a_composition_change(db: Session) -> None:
    """The rebuild also runs when the composition hash moves. Positions
    that did not change keep their flag; the changed one is rebuilt from
    detection because its fixture is now a different match."""
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 7,
        )
    )
    assert row is not None
    _rule(db, row, True)

    # Same 14 positions, different away teams — composition hash moves.
    repo.upsert_slate(_payload(away_prefix="Replacement"))
    db.commit()

    assert _flags(db, slate.id)[7] is True


def test_new_slate_in_a_cup_auto_flags_every_position(db: Session) -> None:
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload(draw_code="PG-KO-CUP", competition="Copa del Rey"))
    db.commit()

    flags = _flags(db, slate.id)
    assert len(flags) == 14
    assert all(flags.values())


def test_new_slate_in_a_group_stage_competition_stays_unflagged(db: Session) -> None:
    """Abstaining must not be coerced into True — an unflagged position
    is the safe default, since the flag only trims the draw."""
    repo = SlateRepository(db)
    slate = repo.upsert_slate(
        _payload(draw_code="PG-KO-UCL", competition="UEFA Champions League")
    )
    db.commit()

    assert not any(_flags(db, slate.id).values())


# ---------------------------------------------------------------------------
# Stage-based detection — the signal that competition names cannot provide
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stage",
    [
        "Final",
        "Semi-Final",
        "Quarter-Final",
        "Round of 16",
        "semifinal",
        "Play-Offs",
        "Liguilla",
        "Octavos de Final",
        "Knockout Round Play-offs",
    ],
)
def test_knockout_stages_detected(stage: str) -> None:
    assert detect_knockout_from_stage(stage) is True


@pytest.mark.parametrize("stage", ["1", "17", "38", "Group Stage", "Fase de Grupos",
                                   "Regular Season", "League Phase"])
def test_league_and_group_stages_are_not_knockout(stage: str) -> None:
    assert detect_knockout_from_stage(stage) is False


@pytest.mark.parametrize("stage", [None, "", "   ", "Matchday", "Apertura"])
def test_uninformative_stage_abstains(stage: str | None) -> None:
    assert detect_knockout_from_stage(stage) is None


def test_stage_beats_competition_name() -> None:
    """The two cases the competition name gets wrong in production."""
    # A Liga MX liguilla final — name says plain league.
    assert detect_knockout("Liga MX") is False
    assert resolve_knockout("Liga MX", "Final") is True
    # A Champions League group match — name abstains, stage settles it.
    assert detect_knockout("UEFA Champions League") is None
    assert resolve_knockout("UEFA Champions League", "Group Stage") is False


def test_competition_name_used_when_no_stage_reported() -> None:
    assert resolve_knockout("Copa Chile", None) is True
    assert resolve_knockout("Liga MX", None) is False
    assert resolve_knockout("Copa Libertadores", "") is None


# ---------------------------------------------------------------------------
# Late-arriving stage revises auto flags but never operator rulings
# ---------------------------------------------------------------------------

def _link_stage_document(session: Session, match_id: str, stage: str) -> None:
    """Simulate the fixture feed linking a document that reports a stage."""
    import json as _json

    from app.models.tables import IngestionRunModel, SourceDocumentModel, SourceModel
    from app.repositories.evidence_repository import EvidenceRepository

    source = SourceModel(name=f"tsdb-{stage}", base_url="http://x", kind="scores")
    session.add(source)
    session.flush()
    run = IngestionRunModel(source_id=source.id, status="completed",
                            started_at=datetime.now(timezone.utc))
    session.add(run)
    session.flush()
    document = SourceDocumentModel(
        ingestion_run_id=run.id,
        source_id=source.id,
        external_url="http://x/1",
        title="fixture",
        summary="fixture",
        payload_json=_json.dumps({"fixtures": [{"stage": stage}]}),
        normalized_key=f"key-{stage}",
        captured_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()
    EvidenceRepository(session).create_evidence_for_document(
        document, match_id, summary="fixture", confidence=0.5, payload={}
    )
    session.commit()


def test_late_stage_upgrades_an_auto_flag(db: Session) -> None:
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()
    assert not any(_flags(db, slate.id).values())

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 4,
        )
    )
    assert row is not None
    _link_stage_document(db, row.match_id, "Final")

    assert _flags(db, slate.id)[4] is True


def test_late_stage_does_not_override_an_operator_clear(db: Session) -> None:
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 4,
        )
    )
    assert row is not None
    row.is_knockout = False
    row.knockout_source = "operator"
    db.commit()

    _link_stage_document(db, row.match_id, "Final")

    assert _flags(db, slate.id)[4] is False, "stage overrode a human ruling"


def test_match_stage_is_recorded_once_and_not_rewritten(db: Session) -> None:
    from app.models.tables import MatchModel

    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 2,
        )
    )
    assert row is not None
    _link_stage_document(db, row.match_id, "Semi-Final")
    assert db.get(MatchModel, row.match_id).stage == "Semi-Final"

    # A later, lower-quality document must not rewrite a resolved phase.
    _link_stage_document(db, row.match_id, "Group Stage")
    assert db.get(MatchModel, row.match_id).stage == "Semi-Final"


def test_auto_flag_is_recomputed_not_preserved(db: Session) -> None:
    """An 'auto' flag carries no human intent, so a rebuild is free to
    recompute it — otherwise a stale inference would outlive the
    evidence that produced it."""
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_payload())
    db.commit()

    row = db.scalar(
        select(ProgolSlateMatchModel).where(
            ProgolSlateMatchModel.slate_id == slate.id,
            ProgolSlateMatchModel.position == 9,
        )
    )
    assert row is not None
    row.is_knockout = True  # no attribution: still 'auto'
    db.commit()

    repo.upsert_slate(_payload())
    db.commit()

    assert _flags(db, slate.id)[9] is False
