from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.tables import MatchFeatureSnapshotModel, PredictionModel
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.slate_readiness_report_service import build_slate_readiness_report


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'readiness.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _seed_readiness_case(
    session: Session,
    *,
    recent: int,
    h2h: int,
) -> str:
    now = datetime.now(timezone.utc)
    slate = SlateRepository(session).upsert_slate(
        ProgolSlateCreate(
            label="PG test",
            draw_code="PG-RDY",
            week_type="weekend",
            registration_closes_at=now + timedelta(days=2),
            matches=[
                MatchReferencePayload(
                    position=1,
                    competition=CompetitionPayload(name="Liga MX"),
                    home_team=TeamPayload(name="Home"),
                    away_team=TeamPayload(name="Away"),
                    kickoff_at=now + timedelta(days=3),
                )
            ],
        )
    )
    match = slate.matches[0].match
    session.add(
        PredictionModel(
            match_id=match.id,
            slate_id=slate.id,
            composition_hash=slate.composition_hash,
            slate_version=slate.slate_version,
            generated_at=now,
            home_probability=0.58,
            draw_probability=0.22,
            away_probability=0.20,
            recommended_outcome="1",
            confidence_band="high",
            sanity_audit_json=json.dumps(
                {
                    "decision_probabilities": {"L": 0.58, "E": 0.22, "V": 0.20},
                    "final_status": "LISTO",
                    "evidence_level": "high",
                    "sanity_flags": ["FALLBACK_USED"],
                }
            ),
        )
    )
    session.add(
        MatchFeatureSnapshotModel(
            match_id=match.id,
            generated_at=now,
            feature_set_version="test",
            payload_json=json.dumps(
                {
                    "recent_results_count": recent,
                    "head_to_head_results_count": h2h,
                }
            ),
        )
    )
    session.commit()
    return slate.id


def test_fallback_only_without_recent_context_is_degraded_to_revisar(db):
    slate_id = _seed_readiness_case(db, recent=0, h2h=0)

    report = build_slate_readiness_report(db, include_archived=True, slate_ids={slate_id})
    match = report["slates"][0]["matches"][0]

    assert match["status"] == "REVISAR"
    assert "FALLBACK_ONLY_NO_RECENT_CONTEXT" in match["flags"]
    assert "model_fallback" in match["actionable_blockers"]
    assert report["slates"][0]["status_counts"] == {"REVISAR": 1}


def test_fallback_with_recent_context_keeps_original_status(db):
    slate_id = _seed_readiness_case(db, recent=3, h2h=0)

    report = build_slate_readiness_report(db, include_archived=True, slate_ids={slate_id})
    match = report["slates"][0]["matches"][0]

    assert match["status"] == "LISTO"
    assert "FALLBACK_ONLY_NO_RECENT_CONTEXT" not in match["flags"]
