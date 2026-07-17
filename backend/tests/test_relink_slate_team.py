"""Guarded generic slate-team relink (scripts.relink_slate_team).

Successor of the PG-2338-specific relink tooling; these tests pin the same
safety contract: dry-run by default, explicit token, placeholder-only source,
canonical-only target, collision refusal, in-place update preserving the
match PK and attached predictions.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CompetitionModel,
    MatchModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    TeamModel,
)
from scripts.relink_slate_team import CONFIRM_TOKEN, main


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'relink_team.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _seed(session: Session, *, away_placeholder: bool = True) -> tuple[ProgolSlateModel, MatchModel]:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    spain = TeamModel(name="Spain", is_placeholder=False)
    slot = TeamModel(name="Ganador E.U.A.", is_placeholder=away_placeholder)
    belgium = TeamModel(name="Bélgica", is_placeholder=False)
    session.add_all([comp, spain, slot, belgium])
    session.flush()
    match = MatchModel(
        competition_id=comp.id,
        home_team_id=spain.id,
        away_team_id=slot.id,
        kickoff_at=datetime(2026, 7, 10, 19, 0, tzinfo=timezone.utc),
    )
    session.add(match)
    session.flush()
    slate = ProgolSlateModel(
        label="PGM-999",
        draw_code="PGM-999",
        week_type="media_semana",
        registration_closes_at=datetime(2026, 7, 7, 19, 0, tzinfo=timezone.utc),
        slate_version=1,
        composition_hash="cafecafe",
    )
    session.add(slate)
    session.flush()
    session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=4))
    session.commit()
    return slate, match


def _argv(extra: list[str] | None = None) -> list[str]:
    base = [
        "--draw-code", "PGM-999", "--position", "4",
        "--side", "away", "--target-team", "Bélgica",
    ]
    return base + (extra or [])


def test_dry_run_reports_and_does_not_write(db, capsys) -> None:
    _, match = _seed(db)
    away_before = match.away_team_id
    assert main(_argv(["--dry-run"])) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "Bélgica" in out
    db.expire_all()
    assert db.get(MatchModel, match.id).away_team_id == away_before


def test_apply_requires_exact_token(db) -> None:
    _, match = _seed(db)
    away_before = match.away_team_id
    # --apply without token and with a wrong token both stay read-only.
    assert main(_argv(["--apply"])) == 0
    assert main(_argv(["--apply", "--confirm", "yes"])) == 0
    db.expire_all()
    assert db.get(MatchModel, match.id).away_team_id == away_before


def test_blocks_when_current_team_is_not_placeholder(db) -> None:
    _seed(db, away_placeholder=False)
    assert main(_argv(["--apply", "--confirm", CONFIRM_TOKEN])) == 4


def test_blocks_when_target_missing_or_placeholder(db) -> None:
    _seed(db)
    missing = ["--draw-code", "PGM-999", "--position", "4", "--side", "away",
               "--target-team", "Nadie", "--apply", "--confirm", CONFIRM_TOKEN]
    assert main(missing) == 3
    ghost = TeamModel(name="Fantasma", is_placeholder=True)
    db.add(ghost)
    db.commit()
    placeholder_target = ["--draw-code", "PGM-999", "--position", "4", "--side", "away",
                          "--target-team", "Fantasma", "--apply", "--confirm", CONFIRM_TOKEN]
    assert main(placeholder_target) == 4


def test_blocks_on_fixture_identity_collision(db) -> None:
    slate, match = _seed(db)
    spain = db.scalar(select(TeamModel).where(TeamModel.name == "Spain"))
    belgium = db.scalar(select(TeamModel).where(TeamModel.name == "Bélgica"))
    db.add(
        MatchModel(
            competition_id=match.competition_id,
            home_team_id=spain.id,
            away_team_id=belgium.id,
            kickoff_at=match.kickoff_at,
        )
    )
    db.commit()
    assert main(_argv(["--apply", "--confirm", CONFIRM_TOKEN])) == 5
    db.expire_all()
    assert db.get(MatchModel, match.id).away_team_id != belgium.id


def test_confirmed_apply_relinks_in_place_preserving_predictions(db) -> None:
    slate, match = _seed(db)
    db.add(
        PredictionModel(
            match_id=match.id,
            slate_id=slate.id,
            composition_hash=slate.composition_hash,
            slate_version=1,
            generated_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
            home_probability=0.4,
            draw_probability=0.3,
            away_probability=0.3,
            recommended_outcome="1",
            confidence_band="low",
            anchors_json="{}",
        )
    )
    db.commit()
    hash_before, version_before = slate.composition_hash, slate.slate_version

    assert main(_argv(["--apply", "--confirm", CONFIRM_TOKEN])) == 0

    db.expire_all()
    slate = db.scalar(select(ProgolSlateModel).where(ProgolSlateModel.draw_code == "PGM-999"))
    relinked = db.get(MatchModel, match.id)
    belgium = db.scalar(select(TeamModel).where(TeamModel.name == "Bélgica"))
    slot = db.scalar(select(TeamModel).where(TeamModel.name == "Ganador E.U.A."))
    assert relinked.away_team_id == belgium.id  # in-place relink
    assert slate.composition_hash == hash_before  # invariant
    assert slate.slate_version == version_before  # invariant
    assert slot is not None  # placeholder history kept
    preds = db.scalars(select(PredictionModel).where(PredictionModel.match_id == match.id)).all()
    assert len(preds) == 1  # prediction stays attached to the same match PK
