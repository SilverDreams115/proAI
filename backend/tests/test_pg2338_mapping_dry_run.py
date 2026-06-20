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
from app.repositories.entity_repository import EntityRepository
from app.services.entity_resolution_service import EntityResolutionService
from app.services.jornada_scoring_service import JornadaScoringService
from app.services.normalization_service import NormalizationService
from scripts.diagnose_pg2338_mapping import build_mapping_dry_run


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'pg2338_mapping.db'}")
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


def _competition(session: Session) -> CompetitionModel:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    session.add(comp)
    session.flush()
    return comp


def _slate_with_links(
    session: Session,
    *,
    draw_code: str = "PG-2338",
    links: list[tuple[int, TeamModel, TeamModel]],
) -> ProgolSlateModel:
    comp = _competition(session)
    slate = ProgolSlateModel(
        label=draw_code,
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=datetime(2026, 6, 24, 19, 0, tzinfo=timezone.utc),
        slate_version=1,
        composition_hash="old-hash",
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


def test_pg2338_spanish_aliases_normalize_to_canonical_slugs() -> None:
    normalizer = NormalizationService()
    assert normalizer.normalize_team_name("Suiza") == "switzerland"
    assert normalizer.normalize_team_name("Catar") == "qatar"
    assert normalizer.normalize_team_name("Cabo Verde") == "cape-verde"


def test_bosnia_herzegovina_alias_resolves_to_existing_canonical(db) -> None:
    canonical = _team(db, "Bosnia and Herzegovina")
    resolver = EntityResolutionService(EntityRepository(db))

    resolved = resolver.resolve_team("Bosnia-Herzegovina", None, is_placeholder=True)

    assert resolved.id == canonical.id
    assert resolved.is_placeholder is False


def test_republica_del_congo_stays_ambiguous_without_local_evidence(db) -> None:
    congo = _team(db, "Congo")
    dr_congo = _team(db, "DR Congo")
    placeholder = _team(db, "República Del Congo", placeholder=True)
    uzbekistan = _team(db, "Uzbekistan")
    _slate_with_links(db, links=[(13, placeholder, uzbekistan)])
    db.commit()

    report = build_mapping_dry_run(db)
    row = report["rows"][0]

    assert {congo.name, dr_congo.name} == {"Congo", "DR Congo"}
    assert row["position"] == 13
    assert row["status"] == "needs_review_mapping"
    assert row["safe_to_apply"] is False
    assert row["proposed_home_canonical"] is None


def test_dry_run_does_not_write_db(db) -> None:
    switzerland = _team(db, "Switzerland")
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    _slate_with_links(db, links=[(2, suiza, canada)])
    db.commit()
    before = {
        "teams": db.scalar(select(TeamModel).where(TeamModel.id == switzerland.id)).id,
        "team_count": db.query(TeamModel).count(),
        "alias_count": db.query(TeamAliasModel).count(),
        "match_count": db.query(MatchModel).count(),
    }

    report = build_mapping_dry_run(db)

    after = {
        "teams": db.scalar(select(TeamModel).where(TeamModel.id == switzerland.id)).id,
        "team_count": db.query(TeamModel).count(),
        "alias_count": db.query(TeamAliasModel).count(),
        "match_count": db.query(MatchModel).count(),
    }
    assert before == after
    assert report["rows"][0]["safe_to_apply"] is True


def test_safe_relink_dry_run_does_not_touch_unrelated_shared_match(db) -> None:
    _team(db, "Switzerland")
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate_with_links(db, draw_code="PG-2338", links=[(2, suiza, canada)])
    other = ProgolSlateModel(label="Other", draw_code="PG-OTHER", week_type="weekend")
    db.add(other)
    db.flush()
    shared_match_id = slate.matches[0].match_id
    db.add(ProgolSlateMatchModel(slate_id=other.id, match_id=shared_match_id, position=1))
    db.commit()

    report = build_mapping_dry_run(db)
    row = report["rows"][0]

    assert row["would_change_team_id"] is True
    assert row["safe_to_apply"] is False
    assert row["status"] == "blocked_shared_match"


def test_composition_hash_change_keeps_old_predictions_stale(db) -> None:
    _team(db, "Switzerland")
    suiza = _team(db, "Suiza", placeholder=True)
    canada = _team(db, "Canada")
    slate = _slate_with_links(db, links=[(2, suiza, canada)])
    db.flush()
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

    report = build_mapping_dry_run(db)
    proposed_hash = report["slate"]["proposed_safe_hash"]
    latest = JornadaScoringService(db)._latest_predictions(slate.id, proposed_hash, [match_id])

    assert report["would_change_composition_hash"] is True
    assert report["slate"]["stored_hash_matches_model"] is False
    assert report["slate"]["proposed_safe_hash_matches_stored"] is False
    assert report["rows"][0]["would_change_composition_hash"] is True
    assert latest == {}


def test_safe_pg2338_placeholders_are_unique_candidates(db) -> None:
    switzerland = _team(db, "Switzerland")
    qatar = _team(db, "Qatar")
    cape_verde = _team(db, "Cape Verde")
    suiza = _team(db, "Suiza", placeholder=True)
    bosnia = _team(db, "Bosnia-Herzegovina")
    catar = _team(db, "Catar", placeholder=True)
    cabo = _team(db, "Cabo Verde", placeholder=True)
    saudi = _team(db, "Saudi Arabia")
    _slate_with_links(
        db,
        links=[
            (2, suiza, _team(db, "Canada")),
            (3, bosnia, catar),
            (8, cabo, saudi),
        ],
    )
    db.commit()

    report = build_mapping_dry_run(db)
    rows = {row["position"]: row for row in report["rows"]}

    assert rows[2]["proposed_home_canonical"] == switzerland.name
    assert rows[3]["proposed_away_canonical"] == qatar.name
    assert rows[8]["proposed_home_canonical"] == cape_verde.name
    assert report["safe_positions"] == [2, 3, 8]
