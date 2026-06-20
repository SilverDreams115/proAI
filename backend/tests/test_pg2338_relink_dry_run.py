from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamAliasModel
from app.models.tables import TeamModel
from app.services.normalization_service import NormalizationService
from scripts.relink_pg2338_mapping import (
    EXPECTED_COMPOSITION_HASH,
    CONFIRM_TOKEN,
    RelinkAbort,
    apply_relink,
    build_relink_plan,
)


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'pg2338_relink.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _team(session: Session, name: str, *, placeholder: bool = False) -> TeamModel:
    team = TeamModel(name=name, is_placeholder=placeholder)
    session.add(team)
    session.flush()
    if not placeholder:
        session.add(
            TeamAliasModel(
                team_id=team.id,
                alias=name,
                normalized_alias=NormalizationService().normalize_team_name(name),
            )
        )
        session.flush()
    return team


def _slate(
    session: Session,
    *,
    links: list[tuple[int, TeamModel, TeamModel]],
    draw_code: str = "PG-2338",
    composition_hash: str = EXPECTED_COMPOSITION_HASH,
    slate_version: int = 1,
) -> ProgolSlateModel:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    session.add(comp)
    session.flush()
    slate = ProgolSlateModel(
        label=draw_code,
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=datetime(2026, 6, 24, 19, 0, tzinfo=timezone.utc),
        slate_version=slate_version,
        composition_hash=composition_hash,
    )
    session.add(slate)
    session.flush()
    for position, home, away in links:
        match = MatchModel(
            competition_id=comp.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=datetime(2026, 6, 25, 7, 0, tzinfo=timezone.utc)
            + timedelta(hours=position),
        )
        session.add(match)
        session.flush()
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=position))
    session.flush()
    return slate


def _safe_slate(session: Session) -> ProgolSlateModel:
    switzerland = _team(session, "Switzerland")
    suiza = _team(session, "Suiza", placeholder=True)
    bosnia = _team(session, "Bosnia-Herzegovina")
    qatar = _team(session, "Qatar")
    catar = _team(session, "Catar", placeholder=True)
    cape_verde = _team(session, "Cape Verde")
    cabo = _team(session, "Cabo Verde", placeholder=True)
    saudi = _team(session, "Saudi Arabia")
    canada = _team(session, "Canada")
    assert {switzerland.id, qatar.id, cape_verde.id}  # canonical targets exist
    slate = _slate(
        session,
        links=[(2, suiza, canada), (3, bosnia, catar), (8, cabo, saudi)],
    )
    session.commit()
    return slate


# 1. Dry-run detects pos2/3/8 as safe.
def test_dry_run_detects_safe_positions(db) -> None:
    slate = _safe_slate(db)
    plan = build_relink_plan(db, slate.id)
    assert plan["safe_positions"] == [2, 3, 8]
    rows = {r["position"]: r for r in plan["rows"]}
    assert rows[2]["proposed_home_name"] == "Switzerland"
    assert rows[3]["proposed_away_name"] == "Qatar"
    assert rows[8]["proposed_home_name"] == "Cape Verde"
    for pos in (2, 3, 8):
        assert rows[pos]["would_change_team_id"] is True
        assert rows[pos]["would_change_match_id"] is False
        assert rows[pos]["would_touch_predictions"] is False
        assert rows[pos]["would_touch_snapshots"] is False


# 2. Dry-run blocks pos13 ambiguity.
def test_dry_run_blocks_pos13_ambiguity(db) -> None:
    _team(db, "Congo")
    _team(db, "DR Congo")
    placeholder = _team(db, "República Del Congo", placeholder=True)
    uzbekistan = _team(db, "Uzbekistan")
    slate = _slate(db, links=[(13, placeholder, uzbekistan)])
    db.commit()

    plan = build_relink_plan(db, slate.id)
    row = plan["rows"][0]
    assert row["position"] == 13
    assert row["status"] == "needs_review_mapping"
    assert row["safe_to_apply"] is False
    assert plan["safe_positions"] == []
    assert plan["review_positions"] == [13]


# 3. Dry-run aborts if composition_hash is not the expected one.
def test_dry_run_aborts_on_hash_drift(db) -> None:
    switzerland = _team(db, "Switzerland")
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    assert switzerland.id
    slate = _slate(db, links=[(2, suiza, canada)], composition_hash="deadbeef")
    db.commit()
    with pytest.raises(RelinkAbort, match="composition_hash drift"):
        build_relink_plan(db, slate.id)


def test_dry_run_aborts_on_wrong_draw_code(db) -> None:
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate(db, links=[(2, suiza, canada)], draw_code="PG-9999")
    db.commit()
    with pytest.raises(RelinkAbort, match="draw_code"):
        build_relink_plan(db, slate.id)


# 4. Dry-run does not write the DB.
def test_dry_run_does_not_write_db(db) -> None:
    slate = _safe_slate(db)
    before = {
        "teams": db.query(TeamModel).count(),
        "matches": db.query(MatchModel).count(),
        "links": db.query(ProgolSlateMatchModel).count(),
        "home_ids": [m.home_team_id for m in db.scalars(select(MatchModel)).all()],
    }
    build_relink_plan(db, slate.id)
    after = {
        "teams": db.query(TeamModel).count(),
        "matches": db.query(MatchModel).count(),
        "links": db.query(ProgolSlateMatchModel).count(),
        "home_ids": [m.home_team_id for m in db.scalars(select(MatchModel)).all()],
    }
    assert before == after


# 5. --apply requires explicit confirmation and never runs by accident.
def test_apply_requires_confirmation_token(db) -> None:
    slate = _safe_slate(db)
    home_before = {
        m.id: (m.home_team_id, m.away_team_id) for m in db.scalars(select(MatchModel)).all()
    }
    with pytest.raises(RelinkAbort, match="confirmation token"):
        apply_relink(db, slate.id, confirm="")
    with pytest.raises(RelinkAbort, match="confirmation token"):
        apply_relink(db, slate.id, confirm="yes")
    db.rollback()
    home_after = {
        m.id: (m.home_team_id, m.away_team_id) for m in db.scalars(select(MatchModel)).all()
    }
    assert home_before == home_after


# 6. Target canonical must exist and not be a placeholder.
def test_blocks_when_canonical_target_missing(db) -> None:
    # Placeholder with no non-placeholder Switzerland to resolve to.
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate(db, links=[(2, suiza, canada)])
    db.commit()
    plan = build_relink_plan(db, slate.id)
    row = plan["rows"][0]
    assert row["safe_to_apply"] is False
    assert row["status"] == "provider_missing"


def test_blocks_when_only_candidate_is_placeholder(db) -> None:
    # A same-normalized team that is itself a placeholder must not be a target.
    _team(db, "Switzerland", placeholder=True)  # placeholder -> excluded
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate(db, links=[(2, suiza, canada)])
    db.commit()
    plan = build_relink_plan(db, slate.id)
    assert plan["rows"][0]["safe_to_apply"] is False
    assert plan["rows"][0]["status"] == "provider_missing"


# 7. Multiple canonical candidates block the relink.
def test_blocks_on_multiple_candidates(db) -> None:
    # normalized_alias is globally unique, so two candidates can only arise via
    # the name-match vs alias-match split: candidate A is matched by exact name
    # ("Suiza"), candidate B ("Switzerland") by its normalized alias.
    cand_a = TeamModel(name="Suiza", is_placeholder=False)  # no colliding alias
    db.add(cand_a)
    db.flush()
    switzerland = _team(db, "Switzerland")  # alias normalized "switzerland"
    assert cand_a.id != switzerland.id
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate(db, links=[(2, suiza, canada)])
    db.commit()
    plan = build_relink_plan(db, slate.id)
    assert plan["rows"][0]["safe_to_apply"] is False
    assert plan["rows"][0]["status"] == "needs_review_mapping"


# 8 & 9. A confirmed apply keeps composition_hash and slate_version unchanged,
# preserves match_id, and leaves predictions attached.
def test_confirmed_apply_preserves_hash_version_and_predictions(db) -> None:
    switzerland = _team(db, "Switzerland")
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate(db, links=[(2, suiza, canada)])
    match_id = slate.matches[0].match_id
    db.add(
        PredictionModel(
            match_id=match_id,
            slate_id=slate.id,
            composition_hash=slate.composition_hash,
            slate_version=1,
            generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            home_probability=0.4,
            draw_probability=0.3,
            away_probability=0.3,
            recommended_outcome="1",
            confidence_band="low",
            anchors_json="{}",
        )
    )
    db.commit()

    hash_before = slate.composition_hash
    version_before = slate.slate_version
    result = apply_relink(db, slate.id, confirm=CONFIRM_TOKEN)
    db.commit()

    assert result["applied_positions"] == [2]
    db.refresh(slate)
    match = slate.matches[0].match
    assert match.id == match_id  # PK preserved
    assert match.home_team_id == switzerland.id  # in-place relink
    assert slate.composition_hash == hash_before  # invariant
    assert slate.slate_version == version_before  # invariant
    # Placeholder team is not deleted (no history removal).
    assert db.get(TeamModel, suiza.id) is not None
    # Prediction stays attached to the same match_id.
    preds = db.scalars(
        select(PredictionModel).where(PredictionModel.match_id == match_id)
    ).all()
    assert len(preds) == 1
