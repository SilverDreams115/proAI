"""Tests for JornadaScoringService and the scoring endpoints.

Covers:
  - Scoring completo (14/14 results) → is_complete=True, all metrics non-null.
  - Scoring parcial (some results missing) → is_complete=False, partial metrics.
  - Usa la predicción más reciente por match_id.
  - Ignora predicciones con composition_hash diferente al slate.
  - Ignora snapshots inválidos (is_valid=False).
  - Calcula Brier score correctamente.
  - Calcula métricas por confidence_band.
  - Calcula ticket_hits distinguiendo simple/doble/triple.
  - PG-2336-like sin resultados → 0 evaluables, is_complete=False.
  - Predicciones históricas con slate_id=NULL son ignoradas por el scorer.
  - Upsert: recompute actualiza el registro existente, no crea duplicado.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tables import (
    CompetitionModel,
    MatchModel,
    MatchResultModel,
    PredictionModel,
    ProgolJornadaScoreModel,
    SourceModel,
    TeamModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.jornada_scoring_service import JornadaScoringService


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'scoring_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _make_slate(session: Session, draw_code: str = "PG-SC-1", n: int = 14) -> Any:
    repo = SlateRepository(session)
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="Liga MX"),
            home_team=TeamPayload(name=f"Home{i}"),
            away_team=TeamPayload(name=f"Away{i}"),
            kickoff_at=now + timedelta(days=10),
        )
        for i in range(1, n + 1)
    ]
    slate = repo.upsert_slate(
        ProgolSlateCreate(
            label=f"Test {draw_code}",
            draw_code=draw_code,
            week_type="weekend",
            registration_closes_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            matches=matches,
        )
    )
    session.flush()
    return slate


def _source(session: Session, name: str = "test-src") -> SourceModel:
    existing = session.execute(
        text("SELECT id FROM sources WHERE name = :n"), {"n": name}
    ).scalar_one_or_none()
    if existing:
        return session.get(SourceModel, existing)
    src = SourceModel(name=name, base_url="http://test", kind="thesportsdb_season", parser_profile="generic")
    session.add(src)
    session.flush()
    return src


def _add_prediction(
    session: Session,
    match_id: str,
    slate_id: str | None,
    composition_hash: str | None,
    *,
    recommended_outcome: str = "1",
    confidence_band: str = "medium",
    home_p: float = 0.5,
    draw_p: float = 0.3,
    away_p: float = 0.2,
    generated_at: datetime | None = None,
    slate_version: int | None = 1,
) -> PredictionModel:
    pred = PredictionModel(
        match_id=match_id,
        slate_id=slate_id,
        composition_hash=composition_hash,
        slate_version=slate_version,
        generated_at=generated_at or datetime.now(timezone.utc),
        home_probability=home_p,
        draw_probability=draw_p,
        away_probability=away_p,
        recommended_outcome=recommended_outcome,
        confidence_band=confidence_band,
        anchors_json="{}",
    )
    session.add(pred)
    session.flush()
    return pred


def _add_result(
    session: Session,
    match_id: str,
    source_id: str,
    result_code: str = "1",
    home_goals: int = 1,
    away_goals: int = 0,
    played_at: datetime | None = None,
) -> MatchResultModel:
    r = MatchResultModel(
        match_id=match_id,
        source_id=source_id,
        played_at=played_at or datetime.now(timezone.utc),
        home_goals=home_goals,
        away_goals=away_goals,
        result_code=result_code,
    )
    session.add(r)
    session.flush()
    return r


def _snapshot_payload(slate_id: str, match_ids: list[str], mode: str = "fixed") -> str:
    """Build a minimal snapshot payload with simple=fixed picks."""
    recs = [
        {
            "match_id": mid,
            "position": i + 1,
            "decisions": {
                "simple": {"pick_type": "fixed", "picks": ["1"]},
                "doubles": {"pick_type": "double", "picks": ["1", "X"]},
                "full": {"pick_type": "triple", "picks": ["1", "X", "2"]},
            },
        }
        for i, mid in enumerate(match_ids)
    ]
    return json.dumps({"slate_id": slate_id, "recommendations": recs})


def _add_snapshot(
    session: Session,
    slate_id: str,
    composition_hash: str,
    match_ids: list[str],
    is_valid: bool = True,
) -> TicketRecommendationSnapshotModel:
    snap = TicketRecommendationSnapshotModel(
        slate_id=slate_id,
        model_version="ticket-optimizer-v2",
        payload_json=_snapshot_payload(slate_id, match_ids),
        composition_hash=composition_hash,
        is_valid=is_valid,
    )
    session.add(snap)
    session.flush()
    return snap


# ---------------------------------------------------------------------------
# Brier score unit test (pure math, no DB)
# ---------------------------------------------------------------------------

def test_brier_score_perfect_home_win():
    """Perfect prediction: p=1.0 home → BS=0."""
    bs = JornadaScoringService._brier_score(1.0, 0.0, 0.0, "1")
    assert bs == 0.0


def test_brier_score_worst_case():
    """Assign 0 to the correct outcome, split remaining between wrong two."""
    # result = "1", predicted p=[0, 0.5, 0.5]
    bs = JornadaScoringService._brier_score(0.0, 0.5, 0.5, "1")
    assert round(bs, 4) == round((0 - 1) ** 2 + (0.5 - 0) ** 2 + (0.5 - 0) ** 2, 4)


def test_brier_score_draw():
    """Draw result with moderate draw probability."""
    bs = JornadaScoringService._brier_score(0.3, 0.5, 0.2, "X")
    expected = round((0.3 - 0) ** 2 + (0.5 - 1) ** 2 + (0.2 - 0) ** 2, 4)
    assert bs == expected


def test_brier_score_away():
    bs = JornadaScoringService._brier_score(0.3, 0.3, 0.4, "2")
    expected = round((0.3 - 0) ** 2 + (0.3 - 0) ** 2 + (0.4 - 1) ** 2, 4)
    assert bs == expected


# ---------------------------------------------------------------------------
# Scoring with no results (PG-2336 pattern)
# ---------------------------------------------------------------------------

def test_scoring_no_results_is_not_complete(db):
    """Slate with predictions but no results → 0 evaluable, is_complete=False."""
    slate = _make_slate(db)
    db.commit()

    match_ids = [sm.match_id for sm in slate.matches]
    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash)
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.total_matches == 14
    assert score.matches_with_results == 0
    assert score.simple_hits == 0
    assert score.simple_hit_rate is None
    assert score.brier_score_avg is None
    assert score.is_complete is False
    assert score.ticket_hits is None  # no snapshot


# ---------------------------------------------------------------------------
# Complete 14/14 scoring
# ---------------------------------------------------------------------------

def test_scoring_complete_14_of_14(db):
    """All 14 matches with predictions + results → is_complete=True, rate non-null."""
    slate = _make_slate(db)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]

    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")
        _add_result(db, mid, src.id, result_code="1")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.total_matches == 14
    assert score.matches_with_results == 14
    assert score.simple_hits == 14
    assert score.simple_hit_rate == 1.0
    assert score.brier_score_avg is not None
    assert score.is_complete is True


# ---------------------------------------------------------------------------
# Partial scoring (some results missing)
# ---------------------------------------------------------------------------

def test_scoring_partial_missing_results(db):
    """Only 7/14 results → is_complete=False, partial hit_rate."""
    slate = _make_slate(db)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]

    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")
    for mid in match_ids[:7]:
        _add_result(db, mid, src.id, result_code="1")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.matches_with_results == 7
    assert score.simple_hits == 7
    assert score.simple_hit_rate == 1.0
    assert score.is_complete is False


# ---------------------------------------------------------------------------
# Uses the latest prediction when multiple exist
# ---------------------------------------------------------------------------

def test_scoring_uses_latest_prediction(db):
    """Two predictions for the same match: scorer picks the more recent one."""
    slate = _make_slate(db, n=1)
    src = _source(db)
    db.commit()

    match_id = slate.matches[0].match_id
    now = datetime.now(timezone.utc)

    # Old prediction: says "2" (wrong)
    _add_prediction(
        db, match_id, slate.id, slate.composition_hash,
        recommended_outcome="2",
        generated_at=now - timedelta(hours=2),
    )
    # New prediction: says "1" (correct)
    _add_prediction(
        db, match_id, slate.id, slate.composition_hash,
        recommended_outcome="1",
        generated_at=now,
    )
    _add_result(db, match_id, src.id, result_code="1")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.simple_hits == 1


# ---------------------------------------------------------------------------
# Ignores predictions with wrong composition_hash
# ---------------------------------------------------------------------------

def test_scoring_ignores_wrong_composition_hash(db):
    """Prediction with a different composition_hash is not counted."""
    slate = _make_slate(db, n=1)
    src = _source(db)
    db.commit()

    match_id = slate.matches[0].match_id

    # Prediction with a stale/wrong hash
    _add_prediction(
        db, match_id, slate.id, "deadbeef" * 8,
        recommended_outcome="1",
    )
    _add_result(db, match_id, src.id, result_code="1")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    # Result exists but no matching prediction → hit = None, simple_hits = 0
    assert score.matches_with_results == 1
    assert score.simple_hits == 0
    # hit_rate = 0/1 = 0.0 (not None — there IS a result, just no matching prediction)
    assert score.simple_hit_rate == 0.0


# ---------------------------------------------------------------------------
# Ignores invalid snapshots
# ---------------------------------------------------------------------------

def test_scoring_ignores_invalid_snapshot(db):
    """A snapshot with is_valid=False yields ticket_hits=None."""
    slate = _make_slate(db, n=2)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in slate.matches]
    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")
        _add_result(db, mid, src.id, result_code="1")
    _add_snapshot(db, slate.id, slate.composition_hash, match_ids, is_valid=False)
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.ticket_hits is None
    assert score.ticket_hit_rate is None


# ---------------------------------------------------------------------------
# Ticket hit calculation — simple mode
# ---------------------------------------------------------------------------

def test_scoring_ticket_hits_simple(db):
    """Snapshot's simple picks hit for every match with result → ticket_hits == total."""
    slate = _make_slate(db, n=3)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]
    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")
        _add_result(db, mid, src.id, result_code="1")
    _add_snapshot(db, slate.id, slate.composition_hash, match_ids)
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.ticket_hits == 3
    assert score.ticket_hit_rate == 1.0


def test_scoring_ticket_hits_double_covers_missed_simple(db):
    """Result "X" missed by simple pick "1" but covered by double pick ["1","X"]."""
    slate = _make_slate(db, n=1)
    src = _source(db)
    db.commit()

    match_id = slate.matches[0].match_id
    _add_prediction(db, match_id, slate.id, slate.composition_hash,
                    recommended_outcome="1", confidence_band="medium")
    _add_result(db, match_id, src.id, result_code="X")  # draw result

    # Add snapshot with double pick ["1", "X"]
    snap_payload = json.dumps({
        "slate_id": slate.id,
        "recommendations": [{
            "match_id": match_id,
            "position": 1,
            "decisions": {
                "simple": {"pick_type": "fixed", "picks": ["1"]},
                "doubles": {"pick_type": "double", "picks": ["1", "X"]},
                "full": {"pick_type": "triple", "picks": ["1", "X", "2"]},
            },
        }],
    })
    snap = TicketRecommendationSnapshotModel(
        slate_id=slate.id,
        model_version="v2",
        payload_json=snap_payload,
        composition_hash=slate.composition_hash,
        is_valid=True,
    )
    db.add(snap)
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    # Simple pick "1" missed
    assert score.simple_hits == 0
    # ticket_hits = simple mode only = 0
    assert score.ticket_hits == 0

    details = json.loads(score.details_json)
    assert details[0]["ticket_modes"]["simple"]["hit"] is False
    assert details[0]["ticket_modes"]["doubles"]["hit"] is True
    assert details[0]["ticket_modes"]["full"]["hit"] is True


# ---------------------------------------------------------------------------
# Confidence band metrics
# ---------------------------------------------------------------------------

def test_scoring_confidence_band_metrics(db):
    """Hits are attributed correctly per confidence band."""
    slate = _make_slate(db, n=4)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]
    bands = ["high", "medium", "low", "blocked"]
    results = ["1", "1", "2", "X"]    # high→hit, medium→hit, low→miss, blocked→miss

    for mid, band, res_code in zip(match_ids, bands, results):
        _add_prediction(db, mid, slate.id, slate.composition_hash,
                        recommended_outcome="1", confidence_band=band)
        _add_result(db, mid, src.id, result_code=res_code)
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    assert score.high_confidence_hits == 1
    assert score.high_confidence_total == 1
    assert score.medium_confidence_hits == 1
    assert score.medium_confidence_total == 1
    assert score.low_confidence_hits == 0
    assert score.low_confidence_total == 1
    assert score.blocked_hits == 0
    assert score.blocked_total == 1


# ---------------------------------------------------------------------------
# Historical predictions with slate_id=NULL are ignored
# ---------------------------------------------------------------------------

def test_scoring_ignores_predictions_with_null_slate_id(db):
    """A prediction with slate_id=NULL (pre-v11) is not counted for the slate."""
    slate = _make_slate(db, n=1)
    src = _source(db)
    db.commit()

    match_id = slate.matches[0].match_id

    # Legacy prediction: no slate_id, no composition_hash
    _add_prediction(db, match_id, None, None, recommended_outcome="1")
    _add_result(db, match_id, src.id, result_code="1")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    # Result exists, but no matching linked prediction → simple_hits = 0
    assert score.matches_with_results == 1
    assert score.simple_hits == 0


# ---------------------------------------------------------------------------
# Repository upsert
# ---------------------------------------------------------------------------

def test_upsert_updates_existing_row(db):
    """Calling upsert_score twice for the same (slate_id, composition_hash) updates."""
    slate = _make_slate(db)
    db.commit()

    repo = JornadaScoreRepository(db)
    now = datetime.now(timezone.utc)

    _band_zeros = dict(
        high_confidence_hits=0, high_confidence_total=0,
        medium_confidence_hits=0, medium_confidence_total=0,
        low_confidence_hits=0, low_confidence_total=0,
        blocked_hits=0, blocked_total=0,
    )
    s1 = ProgolJornadaScoreModel(
        slate_id=slate.id,
        draw_code=slate.draw_code,
        week_type="weekend",
        composition_hash=slate.composition_hash,
        slate_version=1,
        total_matches=14,
        matches_with_results=7,
        simple_hits=5,
        details_json="[]",
        computed_at=now,
        is_complete=False,
        **_band_zeros,
    )
    saved1 = repo.upsert_score(s1)
    db.commit()

    s2 = ProgolJornadaScoreModel(
        slate_id=slate.id,
        draw_code=slate.draw_code,
        week_type="weekend",
        composition_hash=slate.composition_hash,
        slate_version=1,
        total_matches=14,
        matches_with_results=14,
        simple_hits=10,
        details_json="[]",
        computed_at=now,
        is_complete=True,
        **_band_zeros,
    )
    saved2 = repo.upsert_score(s2)
    db.commit()

    # Same row id, updated fields
    assert saved1.id == saved2.id
    count = db.execute(
        text("SELECT COUNT(*) FROM progol_jornada_scores WHERE slate_id = :sid"),
        {"sid": slate.id},
    ).scalar_one()
    assert count == 1
    assert saved2.matches_with_results == 14
    assert saved2.simple_hits == 10
    assert saved2.is_complete is True


# ---------------------------------------------------------------------------
# No composition_hash → ValueError
# ---------------------------------------------------------------------------

def test_scoring_raises_if_no_composition_hash(db):
    """compute_for_slate raises ValueError when slate.composition_hash is None."""
    slate = _make_slate(db)
    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE id = :sid"),
        {"sid": slate.id},
    )
    db.commit()
    db.expire_all()

    slate_fresh = db.get(
        __import__("app.models.tables", fromlist=["ProgolSlateModel"]).ProgolSlateModel,
        slate.id,
    )
    svc = JornadaScoringService(db)
    with pytest.raises(ValueError, match="composition_hash"):
        svc.compute_for_slate(slate_fresh)


# ---------------------------------------------------------------------------
# Brier score average
# ---------------------------------------------------------------------------

def test_scoring_brier_score_average(db):
    """Average Brier score across two matches computed correctly."""
    slate = _make_slate(db, n=2)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]

    # Match 1: perfect prediction for home win
    _add_prediction(db, match_ids[0], slate.id, slate.composition_hash,
                    home_p=1.0, draw_p=0.0, away_p=0.0, recommended_outcome="1")
    _add_result(db, match_ids[0], src.id, result_code="1")

    # Match 2: wrong prediction (predicted home, actually away)
    _add_prediction(db, match_ids[1], slate.id, slate.composition_hash,
                    home_p=0.6, draw_p=0.25, away_p=0.15, recommended_outcome="1")
    _add_result(db, match_ids[1], src.id, result_code="2")
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)

    bs1 = JornadaScoringService._brier_score(1.0, 0.0, 0.0, "1")
    bs2 = JornadaScoringService._brier_score(0.6, 0.25, 0.15, "2")
    expected_avg = round((bs1 + bs2) / 2, 4)
    assert score.brier_score_avg == expected_avg


# ---------------------------------------------------------------------------
# Details JSON structure
# ---------------------------------------------------------------------------

def test_scoring_details_json_structure(db):
    """details_json contains per-match info including hit, brier_score."""
    slate = _make_slate(db, n=2)
    src = _source(db)
    db.commit()

    match_ids = [sm.match_id for sm in sorted(slate.matches, key=lambda m: m.position)]
    for mid in match_ids:
        _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")
    _add_result(db, match_ids[0], src.id, result_code="1")   # hit
    _add_result(db, match_ids[1], src.id, result_code="X")   # miss
    db.commit()

    svc = JornadaScoringService(db)
    score = svc.compute_for_slate(slate)
    details = json.loads(score.details_json)

    assert len(details) == 2
    assert details[0]["hit"] is True
    assert details[0]["brier_score"] is not None
    assert details[1]["hit"] is False
    assert details[1]["result_code"] == "X"
