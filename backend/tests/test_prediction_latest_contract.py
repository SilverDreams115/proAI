from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities import Outcome
from app.models.tables import PredictionModel, TicketRecommendationSnapshotModel
from app.repositories.slate_repository import SlateRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.jornada_scoring_service import JornadaScoringService
from app.services.ticket_recommendation_service import TicketRecommendationService


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'latest_contract.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _slate_payload(draw_code: str, *, count: int = 14) -> ProgolSlateCreate:
    kickoff = datetime(2026, 6, 24, 20, 0, tzinfo=timezone.utc)
    return ProgolSlateCreate(
        label=f"Progol {draw_code}",
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=kickoff - timedelta(hours=1),
        matches=[
            MatchReferencePayload(
                position=idx,
                competition=CompetitionPayload(name="International Friendlies"),
                home_team=TeamPayload(name=f"{draw_code}-Home-{idx}"),
                away_team=TeamPayload(name=f"{draw_code}-Away-{idx}"),
                kickoff_at=kickoff + timedelta(hours=idx),
            )
            for idx in range(1, count + 1)
        ],
    )


def _create_slate(session: Session, draw_code: str, *, count: int = 14):
    slate = SlateRepository(session).upsert_slate(_slate_payload(draw_code, count=count))
    session.flush()
    return slate


def _prediction_row(
    *,
    prediction_id: str,
    match_id: str,
    slate_id: str,
    composition_hash: str,
    generated_at: datetime,
    home_probability: float,
    blocked_reason: str | None = None,
) -> PredictionModel:
    return PredictionModel(
        id=prediction_id,
        match_id=match_id,
        slate_id=slate_id,
        composition_hash=composition_hash,
        slate_version=1,
        generated_at=generated_at,
        home_probability=home_probability,
        draw_probability=0.20,
        away_probability=round(0.80 - home_probability, 2),
        recommended_outcome=Outcome.HOME.value,
        confidence_band="medium",
        anchors_json="{}",
        blocked_reason=blocked_reason,
    )


def test_latest_prediction_per_match_uses_generated_at_then_id(db):
    slate = _create_slate(db, "PG-LATEST", count=1)
    match_id = slate.matches[0].match_id
    generated_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    db.add_all(
        [
            _prediction_row(
                prediction_id="pred-a",
                match_id=match_id,
                slate_id=slate.id,
                composition_hash=slate.composition_hash,
                generated_at=generated_at,
                home_probability=0.40,
            ),
            _prediction_row(
                prediction_id="pred-z",
                match_id=match_id,
                slate_id=slate.id,
                composition_hash=slate.composition_hash,
                generated_at=generated_at,
                home_probability=0.58,
            ),
        ]
    )
    db.commit()

    latest = JornadaScoringService(db)._latest_predictions(
        slate.id, slate.composition_hash, [match_id]
    )

    assert latest[match_id].id == "pred-z"
    assert latest[match_id].home_probability == 0.58


def test_latest_prediction_contract_collapses_two_generations_to_match_count(db):
    slate = _create_slate(db, "PG-LATEST14", count=14)
    first_gen = datetime(2026, 6, 19, 4, 47, tzinfo=timezone.utc)
    second_gen = datetime(2026, 6, 19, 5, 30, tzinfo=timezone.utc)
    for sm in slate.matches:
        db.add_all(
            [
                _prediction_row(
                    prediction_id=f"old-{sm.position}",
                    match_id=sm.match_id,
                    slate_id=slate.id,
                    composition_hash=slate.composition_hash,
                    generated_at=first_gen,
                    home_probability=0.41,
                ),
                _prediction_row(
                    prediction_id=f"new-{sm.position}",
                    match_id=sm.match_id,
                    slate_id=slate.id,
                    composition_hash=slate.composition_hash,
                    generated_at=second_gen,
                    home_probability=0.52,
                ),
            ]
        )
    db.commit()

    latest = JornadaScoringService(db)._latest_predictions(
        slate.id, slate.composition_hash, [sm.match_id for sm in slate.matches]
    )

    assert len(latest) == 14
    assert {row.id for row in latest.values()} == {f"new-{idx}" for idx in range(1, 15)}


def test_save_snapshot_supersedes_previous_same_hash_and_model(db):
    slate = _create_slate(db, "PG-SNAPSHOT", count=1)
    repo = TicketRecommendationRepository(db)

    first = repo.save_snapshot(
        slate_id=slate.id,
        model_version=TicketRecommendationService.MODEL_VERSION,
        payload={"slate_id": slate.id, "recommendations": [], "coverage": [], "rules": {}},
        composition_hash=slate.composition_hash,
    )
    second = repo.save_snapshot(
        slate_id=slate.id,
        model_version=TicketRecommendationService.MODEL_VERSION,
        payload={"slate_id": slate.id, "recommendations": [], "coverage": [], "rules": {}},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    db.refresh(first)
    latest = repo.latest_for_slate(
        slate.id,
        composition_hash=slate.composition_hash,
        model_version=TicketRecommendationService.MODEL_VERSION,
    )

    assert first.is_valid is False
    assert first.invalidation_reason == "superseded_by_new_snapshot"
    assert latest is not None
    assert latest.id == second.id


def test_latest_snapshot_selection_is_deterministic_for_legacy_duplicates(db):
    slate = _create_slate(db, "PG-SNAPSHOT-TIE", count=1)
    generated_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    payload = json.dumps(
        {
            "slate_id": slate.id,
            "generated_at": generated_at.isoformat(),
            "model_version": TicketRecommendationService.MODEL_VERSION,
            "rules": {},
            "recommendations": [],
            "coverage": [],
        },
        sort_keys=True,
    )
    db.add_all(
        [
            TicketRecommendationSnapshotModel(
                id="snap-a",
                slate_id=slate.id,
                model_version=TicketRecommendationService.MODEL_VERSION,
                generated_at=generated_at,
                payload_json=payload,
                composition_hash=slate.composition_hash,
                is_valid=True,
            ),
            TicketRecommendationSnapshotModel(
                id="snap-z",
                slate_id=slate.id,
                model_version=TicketRecommendationService.MODEL_VERSION,
                generated_at=generated_at,
                payload_json=payload,
                composition_hash=slate.composition_hash,
                is_valid=True,
            ),
        ]
    )
    db.commit()

    latest = TicketRecommendationRepository(db).latest_for_slate(
        slate.id,
        composition_hash=slate.composition_hash,
        model_version=TicketRecommendationService.MODEL_VERSION,
    )

    assert latest is not None
    assert latest.id == "snap-z"


@pytest.mark.anyio
async def test_get_predictions_endpoint_does_not_insert_prediction_rows(client) -> None:
    slate_response = await client.post("/api/slates", json=_slate_payload("PG-READ-PRED").model_dump(mode="json"))
    assert slate_response.status_code == 201
    slate_id = slate_response.json()["id"]

    from app.db import session as db_mod

    with db_mod.SessionLocal() as session:
        before = session.execute(text("SELECT COUNT(*) FROM predictions")).scalar_one()

    response = await client.get(f"/api/predictions/slates/{slate_id}")

    with db_mod.SessionLocal() as session:
        after = session.execute(text("SELECT COUNT(*) FROM predictions")).scalar_one()

    assert response.status_code == 200
    assert len(response.json()) == 14
    assert after == before


@pytest.mark.anyio
async def test_get_ticket_endpoint_reuses_latest_snapshot_without_duplication(client) -> None:
    slate_response = await client.post("/api/slates", json=_slate_payload("PG-READ-TICKET").model_dump(mode="json"))
    assert slate_response.status_code == 201
    slate_id = slate_response.json()["id"]

    from app.db import session as db_mod
    from app.models.tables import ProgolSlateModel

    with db_mod.SessionLocal() as session:
        slate = session.get(ProgolSlateModel, slate_id)
        assert slate is not None
        payload = {
            "slate_id": slate.id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": TicketRecommendationService.MODEL_VERSION,
            "rules": {},
            "recommendations": [],
            "coverage": [],
        }
        snapshot = TicketRecommendationSnapshotModel(
            id="existing-snapshot",
            slate_id=slate.id,
            model_version=TicketRecommendationService.MODEL_VERSION,
            payload_json=json.dumps(payload, sort_keys=True),
            composition_hash=slate.composition_hash,
            is_valid=True,
        )
        session.add(snapshot)
        session.commit()
        before = session.execute(
            text("SELECT COUNT(*) FROM ticket_recommendation_snapshots")
        ).scalar_one()

    response = await client.get(f"/api/predictions/slates/{slate_id}/ticket")

    with db_mod.SessionLocal() as session:
        after = session.execute(
            text("SELECT COUNT(*) FROM ticket_recommendation_snapshots")
        ).scalar_one()

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == "existing-snapshot"
    assert after == before
