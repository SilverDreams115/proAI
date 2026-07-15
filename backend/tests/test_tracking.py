"""Phase A — Seguimiento (tracking) service + endpoints.

Covers the six required behaviours:
  1. A finished match maps the canonical result_code to L/E/V.
  2. A pending match stays prediction_status=pending.
  3. A conflicting result becomes learning_status=excluded.
  4. A partial slate counts learning ready / pending correctly.
  5. The tracking API exposes raw / decision / ticket_strategy / actual_result.
  6. Pending matches never become learning-ready.

Read-only: nothing here trains, promotes, or fabricates a result.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.entities import MatchResultStatus
from app.models.tables import (
    MatchResultModel,
    PredictionModel,
    ProgolSlateProposalModel,
    SourceModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.live_result_service import LiveResultService
from app.services.tracking_service import TrackingService


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'tracking_test.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _source(session: Session, name: str, priority: int = 50) -> SourceModel:
    src = SourceModel(
        name=name,
        base_url="http://test",
        kind="thesportsdb_season",
        parser_profile="generic",
        result_source_priority=priority,
    )
    session.add(src)
    session.flush()
    return src


# A persisted sanity-audit with raw DELIBERATELY distinct from decision, so a
# test can prove raw came from the historical audit (not a recompute).
HISTORICAL_AUDIT = {
    "raw_probabilities": {"L": 0.70, "E": 0.20, "V": 0.10},
    "display_probabilities": {"L": 0.60, "E": 0.25, "V": 0.15},
    "decision_probabilities": {"L": 0.60, "E": 0.25, "V": 0.15},
    "ticket_strategy": "DOBLE_RECOMENDADO",
}


def _seed_slate(
    session: Session,
    *,
    draw_code: str,
    n: int = 3,
    closes_at: datetime,
    outcomes: list[str] | None = None,
    with_audit: bool = False,
) -> Any:
    repo = SlateRepository(session)
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="Liga MX"),
            home_team=TeamPayload(name=f"{draw_code}-H{i}"),
            away_team=TeamPayload(name=f"{draw_code}-A{i}"),
            kickoff_at=now - timedelta(hours=2),
        )
        for i in range(1, n + 1)
    ]
    slate = repo.upsert_slate(
        ProgolSlateCreate(
            label=f"Test {draw_code}",
            draw_code=draw_code,
            week_type="weekend",
            registration_closes_at=closes_at,
            matches=matches,
        )
    )
    session.flush()
    slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
    match_ids = [sm.match_id for sm in slate_matches]
    outcomes = outcomes or ["1"] * n
    # Original predictions are made BEFORE cierre — keep generated_at < cierre so
    # the provenance query treats them as historical (and any later recompute,
    # stamped "now", is excluded).
    gen_at = closes_at - timedelta(hours=1)
    for sm, outcome in zip(slate_matches, outcomes, strict=True):
        home_p = 0.6 if outcome == "1" else 0.2
        draw_p = 0.25
        away_p = max(0.0, 1.0 - home_p - draw_p)
        session.add(
            PredictionModel(
                match_id=sm.match_id,
                slate_id=slate.id,
                composition_hash=slate.composition_hash,
                slate_version=1,
                generated_at=gen_at,
                home_probability=home_p,
                draw_probability=draw_p,
                away_probability=away_p,
                recommended_outcome=outcome,
                confidence_band="medium",
                anchors_json="{}",
                sanity_audit_json=json.dumps(HISTORICAL_AUDIT) if with_audit else None,
            )
        )
    recs = [
        {
            "match_id": mid,
            "position": i + 1,
            "decisions": {
                "simple": {"pick_type": "fixed", "picks": [outcomes[i]]},
                "doubles": {"pick_type": "double", "picks": [outcomes[i], "X"]},
                "full": {"pick_type": "triple", "picks": ["1", "X", "2"]},
            },
        }
        for i, mid in enumerate(match_ids)
    ]
    session.add(
        TicketRecommendationSnapshotModel(
            slate_id=slate.id,
            model_version="ticket-optimizer-v2",
            payload_json=json.dumps({"slate_id": slate.id, "recommendations": recs}),
            composition_hash=slate.composition_hash,
            is_valid=True,
        )
    )
    session.flush()
    return slate


def _make_official(session: Session, slate: Any) -> None:
    session.add(
        ProgolSlateProposalModel(
            draw_code=slate.draw_code,
            week_type=slate.week_type,
            source_name="LN Progol Guía",
            source_url="https://www.loterianacional.gob.mx/Progol/Guia.pdf",
            status="promoted",
            promoted_slate_id=slate.id,
        )
    )
    session.flush()


def _match_ids(slate) -> list[str]:
    return [sm.match_id for sm in sorted(slate.matches, key=lambda s: s.position)]


def _final(session: Session, match_id: str, source: SourceModel, home: int, away: int) -> None:
    LiveResultService(session).record_observation(
        match_id=match_id,
        source_id=source.id,
        status=MatchResultStatus.FULL_TIME,
        home_goals=home,
        away_goals=away,
        is_final=True,
    )


def _raw_result(session: Session, match_id: str, source: SourceModel, result_code: str) -> None:
    """Insert a raw canonical-store row directly (used to seed a conflict)."""
    session.add(
        MatchResultModel(
            match_id=match_id,
            source_id=source.id,
            played_at=datetime.now(timezone.utc),
            home_goals=1 if result_code == "1" else 0,
            away_goals=1 if result_code == "2" else 0,
            result_code=result_code,
        )
    )
    session.flush()


def _count_predictions(session: Session, slate_id: str) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(PredictionModel).where(PredictionModel.slate_id == slate_id)
        )
        or 0
    )


def _prediction_service(session: Session):
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService
    from app.services.prediction_service import PredictionService

    training = ModelTrainingService(
        TrainingRepository(session), EntityRepository(session), ResultRepository(session)
    )
    return PredictionService(training)


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=3)


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


def _by_pos(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {m["position"]: m for m in payload["matches"]}


# --------------------------------------------------------------------------
# 1. finished match maps actual_result to L/E/V (+ hit/miss)
# --------------------------------------------------------------------------

def test_finished_match_maps_result_to_letter_and_hit(db):
    slate = _seed_slate(db, draw_code="PG-T1", n=3, closes_at=_past(), outcomes=["1", "1", "1"])
    _make_official(db, slate)
    src = _source(db, "ln")
    ids = _match_ids(slate)
    _final(db, ids[0], src, 2, 0)  # home win -> "1" -> L, pred 1 -> hit
    _final(db, ids[1], src, 1, 1)  # draw     -> "X" -> E, pred 1 -> miss
    _final(db, ids[2], src, 0, 2)  # away win -> "2" -> V, pred 1 -> miss

    payload = TrackingService(db).build_tracking(slate)
    by_pos = _by_pos(payload)

    assert by_pos[1]["actual_result"] == "L"
    assert by_pos[1]["match_status"] == "finished"
    assert by_pos[1]["prediction_status"] == "hit"
    assert by_pos[1]["home_score"] == 2 and by_pos[1]["away_score"] == 0
    assert by_pos[2]["actual_result"] == "E"
    assert by_pos[2]["prediction_status"] == "miss"
    assert by_pos[3]["actual_result"] == "V"
    assert by_pos[3]["prediction_status"] == "miss"

    assert payload["status"] == "complete"
    assert payload["finished_matches"] == 3
    assert payload["scored_matches"] == 3
    assert payload["hits"] == 1 and payload["misses"] == 2
    assert payload["accuracy"] == pytest.approx(0.333, abs=1e-3)


# --------------------------------------------------------------------------
# 2. pending match stays prediction_status=pending
# --------------------------------------------------------------------------

def test_pending_match_is_pending(db):
    slate = _seed_slate(db, draw_code="PG-T2", n=3, closes_at=_future(), outcomes=["1", "1", "1"])
    _make_official(db, slate)

    payload = TrackingService(db).build_tracking(slate)
    for m in payload["matches"]:
        assert m["actual_result"] is None
        assert m["match_status"] == "pending"
        assert m["prediction_status"] == "pending"
        assert m["learning_status"] == "waiting_result"
    assert payload["finished_matches"] == 0
    assert payload["pending_matches"] == 3
    assert payload["scored_matches"] == 0
    assert payload["accuracy"] is None
    assert payload["learning_rows_pending"] == 3


# --------------------------------------------------------------------------
# 3. conflicting result -> learning_status=excluded
# --------------------------------------------------------------------------

def test_conflicting_result_is_excluded(db):
    slate = _seed_slate(db, draw_code="PG-T3", n=2, closes_at=_past(), outcomes=["1", "1"])
    _make_official(db, slate)
    ids = _match_ids(slate)
    clean_src = _source(db, "ln", priority=10)
    _final(db, ids[0], clean_src, 2, 0)  # clean canonical -> ready
    # Match 1: two sources disagree (1 vs X) -> conflict -> excluded.
    src_a = _source(db, "src-a", priority=20)
    src_b = _source(db, "src-b", priority=30)
    _raw_result(db, ids[1], src_a, "1")
    _raw_result(db, ids[1], src_b, "X")

    payload = TrackingService(db).build_tracking(slate)
    by_pos = _by_pos(payload)

    assert payload["has_conflicts"] is True
    assert by_pos[1]["learning_status"] == "ready"
    assert by_pos[2]["learning_status"] == "excluded"
    assert by_pos[2]["excluded_from_training"] is True
    assert by_pos[2]["exclusion_reason"] == "conflicting_results"
    assert payload["learning_rows_excluded"] >= 1


# --------------------------------------------------------------------------
# 4. partial slate counts ready / pending correctly
# --------------------------------------------------------------------------

def test_partial_slate_counts_ready_and_pending(db):
    slate = _seed_slate(db, draw_code="PG-T4", n=4, closes_at=_past(), outcomes=["1", "1", "1", "1"])
    _make_official(db, slate)
    src = _source(db, "ln")
    ids = _match_ids(slate)
    _final(db, ids[0], src, 1, 0)
    _final(db, ids[1], src, 0, 1)
    # ids[2], ids[3] stay pending

    payload = TrackingService(db).build_tracking(slate)
    assert payload["status"] != "complete"
    assert payload["finished_matches"] == 2
    assert payload["pending_matches"] == 2
    assert payload["scored_matches"] == 2
    assert payload["learning_rows_ready"] == 2
    assert payload["learning_rows_pending"] == 2


# --------------------------------------------------------------------------
# 5. tracking API exposes raw / decision / ticket_strategy / actual_result
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tracking_endpoint_exposes_fields(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-T5", n=2, closes_at=_past(), outcomes=["1", "1"])
        _make_official(session, slate)
        src = _source(session, "ln")
        ids = _match_ids(slate)
        _final(session, ids[0], src, 2, 0)
        sid = slate.id
        session.commit()

    resp = await client.get(f"/api/slates/{sid}/tracking")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_matches"] == 2
    m0 = next(m for m in body["matches"] if m["position"] == 1)
    assert m0["actual_result"] == "L"
    assert m0["original_pick"] == "L"
    assert "ticket_strategy" in m0
    assert set(m0["raw_probabilities"]) == {"L", "E", "V"}
    assert set(m0["decision_probabilities"]) == {"L", "E", "V"}

    # /comparison is the same enriched payload.
    comp = await client.get(f"/api/slates/{sid}/comparison")
    assert comp.status_code == 200
    assert comp.json()["matches"][0]["position"] == 1


@pytest.mark.anyio
async def test_live_dashboard_comparison_and_tracking_counts_match(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-T5-DASH", n=2, closes_at=_past(), outcomes=["1", "2"])
        _make_official(session, slate)
        src = _source(session, "ln")
        ids = _match_ids(slate)
        _final(session, ids[0], src, 2, 0)
        _final(session, ids[1], src, 0, 1)
        sid = slate.id
        session.commit()

    dash_resp = await client.get("/api/slates/live/dashboard")
    comp_resp = await client.get(f"/api/slates/{sid}/result-comparison")
    track_resp = await client.get(f"/api/slates/{sid}/tracking")

    assert dash_resp.status_code == 200
    assert comp_resp.status_code == 200
    assert track_resp.status_code == 200

    dash = next(e for e in dash_resp.json()["closed"] if e["slate_id"] == sid)
    comp = comp_resp.json()
    track = track_resp.json()

    assert dash["match_count"] == comp["match_count"] == track["total_matches"] == 2
    assert dash["completed_count"] == comp["completed_count"] == track["finished_matches"] == 2
    assert dash["pending_count"] == comp["pending_count"] == track["pending_matches"] == 0
    assert dash["simple_hits"] == comp["score"]["simple_hits"] == track["hits"] == 2
    assert track["learning_rows_ready"] == 2


@pytest.mark.anyio
async def test_tracking_endpoint_404(client):
    resp = await client.get("/api/slates/nope/tracking")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Read-only guarantee: persist_audit + tracking/comparison write nothing
# --------------------------------------------------------------------------

def test_persist_audit_false_writes_no_audit_row(db):
    slate = _seed_slate(db, draw_code="PG-RO1", n=2, closes_at=_past())
    before = _count_predictions(db, slate.id)
    _prediction_service(db).build_slate_predictions(slate, persist_audit=False)
    assert _count_predictions(db, slate.id) == before


def test_persist_audit_true_writes_audit_row(db):
    slate = _seed_slate(db, draw_code="PG-RO2", n=2, closes_at=_past())
    before = _count_predictions(db, slate.id)
    _prediction_service(db).build_slate_predictions(slate, persist_audit=True)
    assert _count_predictions(db, slate.id) > before


def test_readonly_compute_caches_for_sharing_without_seeding_persist_cache(db):
    """Perf: the three read-only slate GETs share one recompute via a read-only
    cache, but that cache never lets a later persist_audit=True call skip its
    audit write (they are separate caches)."""
    from app.services import prediction_service as ps

    ps.invalidate_slate_prediction_cache()
    slate = _seed_slate(db, draw_code="PG-RO-CACHE", n=2, closes_at=_past())

    # First read-only build seeds ONLY the read-only cache.
    _prediction_service(db).build_slate_predictions(slate, persist_audit=False)
    assert ps._cached_readonly_slate_predictions(slate.id) is not None
    assert ps._cached_slate_predictions(slate.id) is None

    # A second read-only build (e.g. the /ticket or /quality endpoint) reuses
    # the cached result — same object identity, no extra audit rows.
    before = _count_predictions(db, slate.id)
    again = _prediction_service(db).build_slate_predictions(slate, persist_audit=False)
    assert again is ps._cached_readonly_slate_predictions(slate.id)
    assert _count_predictions(db, slate.id) == before

    # Despite the warm read-only cache, a persist_audit=True call MUST still
    # recompute and write its audit row (no cross-cache skip).
    _prediction_service(db).build_slate_predictions(slate, persist_audit=True)
    assert _count_predictions(db, slate.id) > before


def test_invalidate_clears_both_prediction_caches(db):
    from app.services import prediction_service as ps

    ps.invalidate_slate_prediction_cache()
    slate = _seed_slate(db, draw_code="PG-RO-INVAL", n=2, closes_at=_past())
    _prediction_service(db).build_slate_predictions(slate, persist_audit=False)
    _prediction_service(db).build_slate_predictions(slate, persist_audit=True)
    assert ps._cached_readonly_slate_predictions(slate.id) is not None
    assert ps._cached_slate_predictions(slate.id) is not None

    ps.invalidate_slate_prediction_cache(slate.id)
    assert ps._cached_readonly_slate_predictions(slate.id) is None
    assert ps._cached_slate_predictions(slate.id) is None


def test_operational_prediction_audit_blocks_placeholder_publication(db):
    from app.services.operational_prediction_audit_service import (
        OperationalPredictionAuditService,
    )

    slate = _seed_slate(db, draw_code="PG-OPA1", n=1, closes_at=_future(), outcomes=["1"])
    _make_official(db, slate)
    match = sorted(slate.matches, key=lambda sm: sm.position)[0].match
    match.away_team.name = "G"
    match.away_team.is_placeholder = True
    pred = db.scalar(select(PredictionModel).where(PredictionModel.slate_id == slate.id))
    assert pred is not None
    pred.sanity_audit_json = json.dumps(
        {
            "decision_probabilities": {"L": 0.45, "E": 0.30, "V": 0.25},
            "final_status": "BLOQUEADO",
            "evidence_level": "low",
            "sanity_flags": ["PLACEHOLDER_TEAM", "BLOCKED_INSUFFICIENT_DATA"],
            "ticket_strategy": "DOBLE_RECOMENDADO",
        }
    )
    db.flush()

    payload = OperationalPredictionAuditService(db).build(slate_id=slate.id)

    assert payload["mode"] == "operational_prediction_audit"
    assert payload["publish_gate"]["allowed"] is False
    assert payload["publish_gate"]["whatsapp_allowed"] is False
    assert payload["publish_gate"]["blocked_count"] == 1
    assert payload["placeholder_queue"]["count"] == 1
    assert payload["confidence_explainer"]["matches"][0]["components"]["data_quality"]["level"] == "blocked"


@pytest.mark.anyio
async def test_operational_prediction_audit_endpoint(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-OPA2", n=1, closes_at=_future(), outcomes=["1"])
        sid = slate.id
        session.commit()

    resp = await client.get(f"/api/tracking/operational-prediction-audit?slate_id={sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "operational_prediction_audit"
    assert "publish_gate" in body
    assert "freshness_monitor" in body


@pytest.mark.anyio
async def test_tracking_endpoint_writes_no_audit_rows(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-RO3", n=2, closes_at=_past(), outcomes=["1", "1"])
        _make_official(session, slate)
        _final(session, _match_ids(slate)[0], _source(session, "ln"), 2, 0)
        sid = slate.id
        session.commit()
    with SessionLocal() as s:
        before = _count_predictions(s, sid)

    resp = await client.get(f"/api/slates/{sid}/tracking")
    assert resp.status_code == 200

    with SessionLocal() as s:
        assert _count_predictions(s, sid) == before  # 100% read-only


@pytest.mark.anyio
async def test_comparison_endpoint_writes_no_audit_rows(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-RO4", n=2, closes_at=_past(), outcomes=["1", "1"])
        _make_official(session, slate)
        _final(session, _match_ids(slate)[0], _source(session, "ln"), 2, 0)
        sid = slate.id
        session.commit()
    with SessionLocal() as s:
        before = _count_predictions(s, sid)

    resp = await client.get(f"/api/slates/{sid}/comparison")
    assert resp.status_code == 200

    with SessionLocal() as s:
        assert _count_predictions(s, sid) == before  # 100% read-only


@pytest.mark.anyio
async def test_predictions_endpoint_is_read_only(client):
    """GET /api/predictions is a read path; refresh is the explicit audit writer."""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-RO5", n=2, closes_at=_past(), outcomes=["1", "1"])
        sid = slate.id
        session.commit()
    with SessionLocal() as s:
        before = _count_predictions(s, sid)

    resp = await client.get(f"/api/predictions/slates/{sid}")
    assert resp.status_code == 200

    with SessionLocal() as s:
        assert _count_predictions(s, sid) == before


# --------------------------------------------------------------------------
# Sign-only official result: tracking shows hit/miss and classification-ready
# --------------------------------------------------------------------------

def test_sign_only_result_is_not_learning_ready(db):
    slate = _seed_slate(db, draw_code="PG-SO", n=1, closes_at=_past(), outcomes=["1"])
    _make_official(db, slate)
    mid = _match_ids(slate)[0]
    # Sign-only final (no goals) -> match_live_results only, never promoted to
    # the canonical (scored) match_results store. This is the Progol acta case.
    LiveResultService(db).record_observation(
        match_id=mid, source_id=_source(db, "ln").id,
        status=MatchResultStatus.FULL_TIME, result_code="1", is_final=True,
    )
    db.flush()

    payload = TrackingService(db).build_tracking(slate)
    m = _by_pos(payload)[1]
    assert m["match_status"] == "finished"
    assert m["actual_result"] == "L"            # sign surfaces in tracking
    assert m["prediction_status"] == "hit"      # hit/miss still computed
    assert m["learning_status"] == "classification_ready"
    assert m["excluded_from_training"] is False
    assert m["exclusion_reason"] is None
    assert payload["learning_rows_ready"] == 0
    assert payload["learning_rows_sign_only"] == 1


# --------------------------------------------------------------------------
# 6. pending matches never become learning-ready
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Punto 1 — probability provenance / traceability
# --------------------------------------------------------------------------

def test_provenance_persisted_sanity_audit(db):
    slate = _seed_slate(db, draw_code="PG-P1", n=1, closes_at=_past(), outcomes=["1"], with_audit=True)
    _make_official(db, slate)
    _final(db, _match_ids(slate)[0], _source(db, "ln"), 2, 0)

    m = TrackingService(db).build_tracking(slate)["matches"][0]
    assert m["probability_source"] == "persisted_sanity_audit"
    assert m["raw_probabilities_is_historical"] is True
    assert m["decision_probabilities_is_historical"] is True
    # raw came from the stored audit (distinct from decision), proving it is
    # the historical record and not a recompute.
    assert m["raw_probabilities"] == {"L": 0.70, "E": 0.20, "V": 0.10}
    assert m["decision_probabilities"] == {"L": 0.60, "E": 0.25, "V": 0.15}


def test_provenance_recomputed_current_sanity(db):
    slate = _seed_slate(db, draw_code="PG-P2", n=1, closes_at=_past(), outcomes=["1"], with_audit=False)
    _make_official(db, slate)
    _final(db, _match_ids(slate)[0], _source(db, "ln"), 2, 0)

    m = TrackingService(db).build_tracking(slate)["matches"][0]
    assert m["probability_source"] == "recomputed_current_sanity"
    assert m["raw_probabilities_is_historical"] is False
    assert m["decision_probabilities_is_historical"] is False
    assert set(m["raw_probabilities"]) == {"L", "E", "V"}


def test_provenance_decision_only_when_recompute_unavailable(db, monkeypatch):
    slate = _seed_slate(db, draw_code="PG-P3", n=1, closes_at=_past(), outcomes=["1"], with_audit=False)
    _make_official(db, slate)
    _final(db, _match_ids(slate)[0], _source(db, "ln"), 2, 0)
    # Simulate a recompute failure: no current-sanity prediction available.
    monkeypatch.setattr(TrackingService, "_slate_predictions", lambda self, s: {})

    m = TrackingService(db).build_tracking(slate)["matches"][0]
    assert m["probability_source"] == "decision_only"
    assert m["raw_probabilities"] is None
    assert m["raw_probabilities_is_historical"] is False
    # decision still available from the persisted probability column.
    assert m["decision_probabilities"] is not None
    assert m["decision_probabilities_is_historical"] is True


def test_hit_miss_independent_of_recomputed_raw(db, monkeypatch):
    from types import SimpleNamespace

    slate = _seed_slate(db, draw_code="PG-P4", n=1, closes_at=_past(), outcomes=["1"], with_audit=False)
    _make_official(db, slate)
    mid = _match_ids(slate)[0]
    _final(db, mid, _source(db, "ln"), 2, 0)  # home win -> result "1"; pred "1" -> hit

    # A recompute that DISAGREES wildly (says away) must not move hit/miss.
    fake = SimpleNamespace(
        match_id=mid,
        raw_probabilities={"L": 0.01, "E": 0.01, "V": 0.98},
        decision_probabilities={"L": 0.05, "E": 0.10, "V": 0.85},
        ticket_strategy="EVITAR",
    )
    monkeypatch.setattr(TrackingService, "_slate_predictions", lambda self, s: {mid: fake})

    m = TrackingService(db).build_tracking(slate)["matches"][0]
    # hit/miss stays faithful to the ORIGINAL stored pick, not the recompute.
    assert m["prediction_status"] == "hit"
    assert m["original_pick"] == "L"
    # raw display does reflect the (current) recompute — that's observability.
    assert m["raw_probabilities"] == {"L": 0.01, "E": 0.01, "V": 0.98}
    assert m["raw_probabilities_is_historical"] is False


@pytest.mark.anyio
async def test_tracking_endpoint_exposes_provenance_fields(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-P5", n=1, closes_at=_past(), outcomes=["1"], with_audit=True)
        _make_official(session, slate)
        _final(session, _match_ids(slate)[0], _source(session, "ln"), 2, 0)
        sid = slate.id
        session.commit()

    resp = await client.get(f"/api/slates/{sid}/tracking")
    assert resp.status_code == 200
    m0 = resp.json()["matches"][0]
    for field in ("probability_source", "raw_probabilities_is_historical", "decision_probabilities_is_historical"):
        assert field in m0
    assert m0["probability_source"] == "persisted_sanity_audit"


def test_pending_never_learning_ready(db):
    slate = _seed_slate(db, draw_code="PG-T6", n=3, closes_at=_past(), outcomes=["1", "1", "1"])
    _make_official(db, slate)
    src = _source(db, "ln")
    ids = _match_ids(slate)
    _final(db, ids[0], src, 1, 0)  # only one final

    payload = TrackingService(db).build_tracking(slate)
    by_pos = _by_pos(payload)
    assert by_pos[1]["learning_status"] == "ready"
    for pos in (2, 3):
        assert by_pos[pos]["match_status"] == "pending"
        assert by_pos[pos]["learning_status"] == "waiting_result"
        assert by_pos[pos]["learning_status"] != "ready"
    # learning_rows_ready counts ONLY the final canonical match.
    assert payload["learning_rows_ready"] == 1
    assert payload["learning_rows_pending"] == 2
