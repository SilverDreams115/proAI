"""Tests for slate_id / composition_hash / slate_version linkage on PredictionModel.

Covers:
  - New PredictionModel columns exist (slate_id, composition_hash, slate_version).
  - _persist_prediction_audit() saves slate context when provided.
  - _persist_prediction_audit() still works without slate context (NULL stays NULL).
  - build_slate_predictions() propagates slate_id + composition_hash to the DB row.
  - Legacy predictions with NULL slate_id are still readable.
  - Migration v11 adds all three columns idempotently.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'pred_link_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _minimal_prediction_model(**overrides) -> PredictionModel:
    base = dict(
        match_id="match-abc",
        generated_at=datetime.now(timezone.utc),
        home_probability=0.4,
        draw_probability=0.3,
        away_probability=0.3,
        recommended_outcome="1",
        confidence_band="low",
        anchors_json="{}",
    )
    base.update(overrides)
    return PredictionModel(**base)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_prediction_model_has_slate_columns(tmp_path):
    """v11 migration adds slate_id, composition_hash, slate_version to predictions."""
    engine = _setup_engine(tmp_path)
    inspector = inspect(engine)
    column_names = {c["name"] for c in inspector.get_columns("predictions")}
    assert "slate_id" in column_names
    assert "composition_hash" in column_names
    assert "slate_version" in column_names


def test_prediction_model_slate_columns_nullable(tmp_path):
    """All three new columns are nullable (backward-compatible with legacy rows)."""
    engine = _setup_engine(tmp_path)
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("predictions")}
    assert columns["slate_id"]["nullable"] is True
    assert columns["composition_hash"]["nullable"] is True
    assert columns["slate_version"]["nullable"] is True


def test_ix_predictions_slate_id_exists(tmp_path):
    """Index ix_predictions_slate_id is created by migration v11."""
    engine = _setup_engine(tmp_path)
    inspector = inspect(engine)
    index_names = {idx["name"] for idx in inspector.get_indexes("predictions")}
    assert "ix_predictions_slate_id" in index_names


def test_ix_predictions_slate_match_generated_exists(tmp_path):
    """Composite index ix_predictions_slate_match_generated is created."""
    engine = _setup_engine(tmp_path)
    inspector = inspect(engine)
    index_names = {idx["name"] for idx in inspector.get_indexes("predictions")}
    assert "ix_predictions_slate_match_generated" in index_names


# ---------------------------------------------------------------------------
# _persist_prediction_audit saves slate context
# ---------------------------------------------------------------------------

def _make_service_with_session(session):
    """Build a PredictionService whose internal session is the test session."""
    from app.services.prediction_service import PredictionService

    mock_training = MagicMock()
    mock_training.training_repository.session = session
    svc = PredictionService(training_service=mock_training)
    return svc


def _make_match_and_competition(session) -> str:
    """Insert a minimal match row and return its id."""
    from app.models.tables import CompetitionModel, TeamModel, MatchModel

    comp = CompetitionModel(name="Liga MX")
    home = TeamModel(name="Team A")
    away = TeamModel(name="Team B")
    session.add_all([comp, home, away])
    session.flush()

    match = MatchModel(
        competition_id=comp.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    session.add(match)
    session.flush()
    return match.id


def _make_slate(session, draw_code: str = "PG-TEST-1") -> object:
    """Insert a minimal slate row and return the model."""
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate

    repo = SlateRepository(session)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="Liga MX"),
            home_team=TeamPayload(name=f"Home{i}"),
            away_team=TeamPayload(name=f"Away{i}"),
            kickoff_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        for i in range(1, 15)
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


def test_persist_audit_saves_slate_fields(db):
    """_persist_prediction_audit saves slate_id, composition_hash, slate_version."""
    match_id = _make_match_and_competition(db)
    slate = _make_slate(db)
    db.commit()

    svc = _make_service_with_session(db)
    svc._persist_prediction_audit(
        match_id=match_id,
        slate_id=slate.id,
        composition_hash=slate.composition_hash,
        slate_version=slate.slate_version,
        generated_at=datetime.now(timezone.utc),
        home_probability=0.5,
        draw_probability=0.3,
        away_probability=0.2,
        recommended_outcome="1",
        confidence_band="medium",
        competition_readiness="active",
        feature_map={
            "home_recent_matches": 5.0,
            "away_recent_matches": 5.0,
            "head_to_head_matches": 3.0,
            "evidence_count": 0.0,
        },
        # R7.6 lineage contract requires a complete sanity_audit to persist.
        sanity_audit={
            "raw_probabilities": {"L": 0.5, "E": 0.3, "V": 0.2},
            "display_probabilities": {"L": 0.5, "E": 0.3, "V": 0.2},
            "decision_probabilities": {"L": 0.5, "E": 0.3, "V": 0.2},
            "final_status": "LISTO",
            "evidence_level": "medium",
            "sanity_policy_version": "test_v1",
            "model_artifact_id": "test-artifact",
            "fallback_used": False,
        },
    )

    row = db.execute(
        text("SELECT slate_id, composition_hash, slate_version FROM predictions WHERE match_id = :mid"),
        {"mid": match_id},
    ).fetchone()
    assert row is not None
    assert row[0] == slate.id
    assert row[1] == slate.composition_hash
    assert row[2] == slate.slate_version


def test_persist_audit_without_slate_context_is_blocked(db):
    """R7.6 — persisting without slate/lineage is now blocked by the contract.

    (Previously this path persisted a NULL-slate row; the lineage contract
    forbids blind persistence. Historical rows are untouched, but new writes
    must be fully traced.)"""
    from app.domain.prediction_lineage import PredictionLineageError

    match_id = _make_match_and_competition(db)
    db.commit()

    svc = _make_service_with_session(db)
    with pytest.raises(PredictionLineageError) as exc:
        svc._persist_prediction_audit(
            match_id=match_id,
            generated_at=datetime.now(timezone.utc),
            home_probability=0.4,
            draw_probability=0.3,
            away_probability=0.3,
            recommended_outcome="1",
            confidence_band="low",
            competition_readiness="active",
            feature_map={
                "home_recent_matches": 5.0,
                "away_recent_matches": 5.0,
                "head_to_head_matches": 3.0,
                "evidence_count": 0.0,
            },
        )
    assert "slate_id" in str(exc.value)

    # Nothing was persisted.
    row = db.execute(
        text("SELECT slate_id FROM predictions WHERE match_id = :mid"),
        {"mid": match_id},
    ).fetchone()
    assert row is None


def test_legacy_prediction_null_slate_fields_readable(db):
    """A PredictionModel written before v11 (NULL slate fields) is still ORM-readable."""
    match_id = _make_match_and_competition(db)
    db.commit()

    # Insert a "legacy" row via raw SQL (no slate columns populated).
    from uuid import uuid4
    pred_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO predictions "
            "(id, match_id, generated_at, home_probability, draw_probability, away_probability, "
            "recommended_outcome, confidence_band, anchors_json) "
            "VALUES (:id, :mid, :gen, 0.4, 0.3, 0.3, '1', 'low', '{}')"
        ),
        {"id": pred_id, "mid": match_id, "gen": datetime.now(timezone.utc).isoformat()},
    )
    db.commit()

    pred = db.get(PredictionModel, pred_id)
    assert pred is not None
    assert pred.slate_id is None
    assert pred.composition_hash is None
    assert pred.slate_version is None


# ---------------------------------------------------------------------------
# build_slate_predictions propagates slate context
# ---------------------------------------------------------------------------

def test_build_slate_predictions_links_slate(db):
    """build_slate_predictions() persists slate_id + composition_hash for each match."""
    from app.services.prediction_service import PredictionService

    slate = _make_slate(db, draw_code="PG-LINK-1")
    db.commit()

    # Build a minimal training service mock that returns fixed scores.
    mock_training = MagicMock()
    mock_training.training_repository.session = db
    mock_training.score_match.return_value = {"home": 0.5, "draw": 0.3, "away": 0.2}
    mock_training.competition_operating_policy.return_value = {
        "competition_readiness": "active",
        "live_pick_allowed": True,
        "policy_reason": "test",
    }
    mock_training.knockout_shrinkage_bounds.return_value = (0.15, 0.55, {})

    svc = PredictionService(training_service=mock_training)

    # Stub the feature service so it returns a non-blocking feature_map.
    svc.feature_service = MagicMock()
    svc.feature_service.build_model_features.return_value = {
        "home_recent_matches": 5.0,
        "away_recent_matches": 5.0,
        "head_to_head_matches": 3.0,
        "evidence_count": 0.0,
    }

    responses = svc.build_slate_predictions(slate)
    assert len(responses) == 14

    rows = db.execute(
        text("SELECT slate_id, composition_hash, slate_version FROM predictions WHERE slate_id = :sid"),
        {"sid": slate.id},
    ).fetchall()

    assert len(rows) == 14
    for slate_id, comp_hash, sv in rows:
        assert slate_id == slate.id
        assert comp_hash == slate.composition_hash
        assert sv == slate.slate_version
