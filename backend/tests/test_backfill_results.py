"""Phase A.1 — manual results backfill (backend/scripts/backfill_results.py).

Covers the required behaviours:
  1. dry-run writes nothing,
  2. apply writes a valid final result,
  3. a final result maps to actual_result L/E/V,
  4. tracking flips pending -> hit/miss,
  5. learning_status flips waiting_result -> ready,
  6. an existing result is not overwritten without --force,
  7. a conflicting result leaves the match excluded.

Read-through-the-canonical-path: the script writes via LiveResultService, so
these also prove the full ingest -> tracking loop.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

# Load the script module by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "backfill_results",
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_results.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
# Register before exec so dataclasses can resolve the module by __module__.
sys.modules["backfill_results"] = backfill
_SPEC.loader.exec_module(backfill)  # type: ignore[union-attr]


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'backfill_test.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _source(session: Session, name: str, priority: int = 50) -> SourceModel:
    src = SourceModel(
        name=name, base_url="http://test", kind="thesportsdb_season",
        parser_profile="generic", result_source_priority=priority,
    )
    session.add(src)
    session.flush()
    return src


def _seed_slate(session: Session, *, draw_code: str, n: int = 3, closes_at: datetime, outcomes: list[str] | None = None) -> Any:
    repo = SlateRepository(session)
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i, competition=CompetitionPayload(name="Liga MX"),
            home_team=TeamPayload(name=f"{draw_code}-H{i}"), away_team=TeamPayload(name=f"{draw_code}-A{i}"),
            kickoff_at=now - timedelta(hours=2),
        )
        for i in range(1, n + 1)
    ]
    slate = repo.upsert_slate(
        ProgolSlateCreate(label=f"Test {draw_code}", draw_code=draw_code, week_type="weekend",
                          registration_closes_at=closes_at, matches=matches)
    )
    session.flush()
    slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
    match_ids = [sm.match_id for sm in slate_matches]
    outcomes = outcomes or ["1"] * n
    gen_at = closes_at - timedelta(hours=1)
    for sm, outcome in zip(slate_matches, outcomes, strict=True):
        home_p = 0.6 if outcome == "1" else 0.2
        session.add(PredictionModel(
            match_id=sm.match_id, slate_id=slate.id, composition_hash=slate.composition_hash,
            slate_version=1, generated_at=gen_at, home_probability=home_p, draw_probability=0.25,
            away_probability=max(0.0, 1.0 - home_p - 0.25), recommended_outcome=outcome,
            confidence_band="medium", anchors_json="{}",
        ))
    recs = [
        {"match_id": mid, "position": i + 1, "decisions": {
            "simple": {"pick_type": "fixed", "picks": [outcomes[i]]},
            "doubles": {"pick_type": "double", "picks": [outcomes[i], "X"]},
            "full": {"pick_type": "triple", "picks": ["1", "X", "2"]}}}
        for i, mid in enumerate(match_ids)
    ]
    session.add(TicketRecommendationSnapshotModel(
        slate_id=slate.id, model_version="ticket-optimizer-v2",
        payload_json=json.dumps({"slate_id": slate.id, "recommendations": recs}),
        composition_hash=slate.composition_hash, is_valid=True,
    ))
    # Official LN lineage -> comparable_with_results -> learning eligible.
    session.add(ProgolSlateProposalModel(
        draw_code=draw_code, week_type="weekend", source_name="LN Progol Guía",
        source_url="https://www.loterianacional.gob.mx/Progol/Guia.pdf",
        status="promoted", promoted_slate_id=slate.id,
    ))
    session.flush()
    return slate


def _match_ids(slate) -> list[str]:
    return [sm.match_id for sm in sorted(slate.matches, key=lambda s: s.position)]


def _count_results(session: Session, slate) -> int:
    ids = _match_ids(slate)
    return int(session.scalar(select(func.count()).select_from(MatchResultModel).where(MatchResultModel.match_id.in_(ids))) or 0)


def _by_pos(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {m["position"]: m for m in payload["matches"]}


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


# 1 ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(db):
    slate = _seed_slate(db, draw_code="PG-BF1", n=2, closes_at=_past(), outcomes=["1", "1"])
    rows = [{"position": 1, "home_score": 2, "away_score": 0, "status": "finished"}]
    before = _count_results(db, slate)
    report = backfill.run_backfill(db, slate, rows, apply=False)
    assert _count_results(db, slate) == before
    assert report.dry_run is True
    assert report.recorded == 0
    assert report.planned[0]["action"] == "record"  # WOULD record


# 2 + 3 -----------------------------------------------------------------------

def test_apply_writes_valid_final_with_letter(db):
    slate = _seed_slate(db, draw_code="PG-BF2", n=3, closes_at=_past(), outcomes=["1", "1", "1"])
    rows = [
        {"position": 1, "home_score": 2, "away_score": 0, "status": "finished"},  # 1 -> L
        {"position": 2, "home_score": 1, "away_score": 1, "status": "finished"},  # X -> E
        {"position": 3, "home_score": 0, "away_score": 2, "status": "finished"},  # 2 -> V
    ]
    report = backfill.run_backfill(db, slate, rows, apply=True)
    assert report.recorded == 3
    assert _count_results(db, slate) == 3

    by_pos = _by_pos(TrackingService(db).build_tracking(slate))
    assert by_pos[1]["actual_result"] == "L"
    assert by_pos[2]["actual_result"] == "E"
    assert by_pos[3]["actual_result"] == "V"


# 4 + 5 -----------------------------------------------------------------------

def test_tracking_and_learning_flip_after_backfill(db):
    slate = _seed_slate(db, draw_code="PG-BF3", n=2, closes_at=_past(), outcomes=["1", "1"])

    before = _by_pos(TrackingService(db).build_tracking(slate))
    assert before[1]["prediction_status"] == "pending"
    assert before[1]["learning_status"] == "waiting_result"

    rows = [
        {"position": 1, "home_score": 2, "away_score": 0, "status": "finished"},  # pred 1 -> hit
        {"position": 2, "home_score": 0, "away_score": 1, "status": "finished"},  # pred 1 -> miss
    ]
    backfill.run_backfill(db, slate, rows, apply=True)

    after = _by_pos(TrackingService(db).build_tracking(slate))
    assert after[1]["prediction_status"] == "hit"
    assert after[1]["learning_status"] == "ready"
    assert after[2]["prediction_status"] == "miss"
    assert after[2]["learning_status"] == "ready"


# 6 ---------------------------------------------------------------------------

def test_existing_result_not_overwritten_without_force(db):
    slate = _seed_slate(db, draw_code="PG-BF4", n=1, closes_at=_past(), outcomes=["1"])
    rows = [{"position": 1, "home_score": 2, "away_score": 0, "status": "finished"}]
    backfill.run_backfill(db, slate, rows, apply=True)
    assert _count_results(db, slate) == 1

    # Same match again, same outcome, no force -> skipped, no new row.
    report = backfill.run_backfill(db, slate, rows, apply=True)
    assert report.recorded == 0
    assert report.skipped_existing == 1
    assert _count_results(db, slate) == 1


# 7 ---------------------------------------------------------------------------

def test_conflicting_result_excludes_match(db):
    slate = _seed_slate(db, draw_code="PG-BF5", n=1, closes_at=_past(), outcomes=["1"])
    mid = _match_ids(slate)[0]
    # An existing LN final says home win ("1").
    LiveResultService(db).record_observation(
        match_id=mid, source_id=_source(db, "ln").id, status=MatchResultStatus.FULL_TIME,
        home_goals=2, away_goals=0, is_final=True,
    )
    db.flush()

    # Backfill a DIFFERENT outcome without force -> reported, NOT written.
    rows = [{"position": 1, "home_score": 1, "away_score": 1, "status": "finished"}]  # "X"
    report = backfill.run_backfill(db, slate, rows, apply=True)
    assert report.recorded == 0
    assert report.conflicts and report.conflicts[0]["position"] == 1

    # Force it in -> two disagreeing sources -> match excluded from learning.
    report2 = backfill.run_backfill(db, slate, rows, apply=True, force=True)
    assert report2.recorded == 1
    by_pos = _by_pos(TrackingService(db).build_tracking(slate))
    # The match is excluded from learning under conflicting sources; the LN row
    # is never deleted (still 2 disagreeing result rows on record).
    assert by_pos[1]["learning_status"] == "excluded"
    assert by_pos[1]["exclusion_reason"] == "conflicting_results"
    assert by_pos[1]["excluded_from_training"] is True
    assert _count_results(db, slate) == 2


def test_unmapped_and_invalid_rows_reported_not_written(db):
    slate = _seed_slate(db, draw_code="PG-BF6", n=1, closes_at=_past(), outcomes=["1"])
    rows = [
        {"position": 99, "home_score": 1, "away_score": 0, "status": "finished"},  # unmapped
        {"position": 1, "home_score": -1, "away_score": 0, "status": "finished"},  # invalid score
        {"position": 1, "status": "pending"},  # not finished
    ]
    report = backfill.run_backfill(db, slate, rows, apply=True)
    assert report.recorded == 0
    assert 99 in report.unmapped_positions
    assert any(r.get("error") == "invalid_scores" for r in report.invalid_rows)
    assert _count_results(db, slate) == 0
