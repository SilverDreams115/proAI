"""Tests for AdaptiveDatasetService and canonical result selection.

Covers:
  - Dataset ignores conflicting results (sources disagree on result_code).
  - Dataset uses canonical result when one source has higher priority.
  - Dataset ignores predictions with slate_id=NULL.
  - Dataset ignores predictions with wrong composition_hash.
  - Dataset rows include brier_score and hit.
  - Dataset rows include ticket picks when a valid snapshot exists.
  - PG-2336 without results → empty row list, valid empty summary.
  - Complete jornada (all matches scored) → N trainable rows.
  - build_summary aggregates across multiple slates.
  - include_partial=True exposes rows from incomplete jornadas.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tables import (
    MatchResultModel,
    PredictionModel,
    ProgolJornadaScoreModel,
    SourceModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.adaptive_dataset_service import AdaptiveDatasetService
from app.services.jornada_scoring_service import JornadaScoringService


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'adaptive_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


def _make_slate(session: Session, draw_code: str = "PG-ADS-1", n: int = 3) -> Any:
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
    slate = SlateRepository(session).upsert_slate(
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


def _source(session: Session, name: str = "src-a", priority: int = 50) -> SourceModel:
    existing = session.execute(
        text("SELECT id FROM sources WHERE name = :n"), {"n": name}
    ).scalar_one_or_none()
    if existing:
        src = session.get(SourceModel, existing)
        src.result_source_priority = priority
        session.flush()
        return src
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


def _add_prediction(
    session: Session,
    match_id: str,
    slate_id: str | None,
    composition_hash: str | None,
    *,
    recommended_outcome: str = "1",
    confidence_band: str = "medium",
    home_p: float = 0.6,
    draw_p: float = 0.25,
    away_p: float = 0.15,
    generated_at: datetime | None = None,
) -> PredictionModel:
    pred = PredictionModel(
        match_id=match_id,
        slate_id=slate_id,
        composition_hash=composition_hash,
        slate_version=1,
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
) -> MatchResultModel:
    r = MatchResultModel(
        match_id=match_id,
        source_id=source_id,
        played_at=datetime.now(timezone.utc),
        home_goals=1 if result_code == "1" else 0,
        away_goals=1 if result_code == "2" else 0,
        result_code=result_code,
    )
    session.add(r)
    session.flush()
    return r


def _score_slate(session: Session, slate: Any) -> ProgolJornadaScoreModel:
    """Compute and persist a jornada score for the given slate."""
    score = JornadaScoringService(session).compute_for_slate(slate)
    score = JornadaScoreRepository(session).upsert_score(score)
    session.commit()
    return score


def _snapshot_payload(slate_id: str, match_ids: list[str]) -> str:
    recs = [
        {
            "match_id": mid,
            "decisions": {
                "simple": {"pick_type": "fixed", "picks": ["1"]},
                "doubles": {"pick_type": "double", "picks": ["1", "X"]},
                "full": {"pick_type": "triple", "picks": ["1", "X", "2"]},
            },
        }
        for mid in match_ids
    ]
    return json.dumps({"recommendations": recs})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDatasetIgnoresConflicts:
    """Matches with conflicting results from two sources are excluded."""

    def test_conflicting_match_excluded_from_rows(self, db: Session):
        slate = _make_slate(db, "PG-CONF-1", n=2)
        slate_id = slate.id
        hash_ = slate.composition_hash
        match_ids = [sm.match_id for sm in slate.matches]

        src_a = _source(db, "conflict-src-a", priority=10)
        src_b = _source(db, "conflict-src-b", priority=20)

        # Match 0: both sources agree → canonical
        _add_result(db, match_ids[0], src_a.id, result_code="1")
        _add_result(db, match_ids[0], src_b.id, result_code="1")

        # Match 1: sources disagree → conflict → must be excluded
        _add_result(db, match_ids[1], src_a.id, result_code="1")
        _add_result(db, match_ids[1], src_b.id, result_code="X")

        for mid in match_ids:
            _add_prediction(db, mid, slate_id, hash_, recommended_outcome="1")

        score = _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(
            slate_id, include_partial=True
        )
        # Only the non-conflicting match appears
        assert len(rows) == 1
        assert rows[0].match_id == match_ids[0]
        assert rows[0].result_is_canonical is True

    def test_summary_counts_conflict_rows(self, db: Session):
        slate = _make_slate(db, "PG-CONF-2", n=2)
        match_ids = [sm.match_id for sm in slate.matches]
        src_a = _source(db, "sum-conf-a", priority=10)
        src_b = _source(db, "sum-conf-b", priority=20)

        _add_result(db, match_ids[0], src_a.id, result_code="1")
        _add_result(db, match_ids[1], src_a.id, result_code="1")
        _add_result(db, match_ids[1], src_b.id, result_code="X")

        for mid in match_ids:
            _add_prediction(db, mid, slate.id, slate.composition_hash)

        _score_slate(db, slate)

        summary = AdaptiveDatasetService(db).build_summary(include_partial=True)
        assert summary.rows_with_conflict >= 1


class TestCanonicalSourcePriority:
    """When sources agree, the one with the lowest priority number wins."""

    def test_canonical_uses_highest_priority_source(self, db: Session):
        slate = _make_slate(db, "PG-PRI-1", n=1)
        match_id = slate.matches[0].match_id
        # Lower number = higher authority
        src_hi = _source(db, "pri-hi-src", priority=10)
        src_lo = _source(db, "pri-lo-src", priority=90)

        # Both agree on result "1" → canonical should come from priority 10
        _add_result(db, match_id, src_hi.id, result_code="1")
        _add_result(db, match_id, src_lo.id, result_code="1")
        _add_prediction(db, match_id, slate.id, slate.composition_hash, recommended_outcome="1")

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(
            slate.id, include_partial=True
        )
        assert len(rows) == 1
        assert rows[0].actual_result == "1"


class TestDatasetFiltersNullSlateId:
    """Predictions with slate_id=NULL must not produce trainable rows."""

    def test_null_slate_id_prediction_excluded(self, db: Session):
        slate = _make_slate(db, "PG-NULL-1", n=1)
        match_id = slate.matches[0].match_id
        src = _source(db, "null-sid-src")

        _add_result(db, match_id, src.id, result_code="1")

        # Only add a NULL-slate_id prediction — scoring service will find nothing
        _add_prediction(db, match_id, slate_id=None, composition_hash=None)

        # Score will show match_with_results=1 but no prediction → hit=None,
        # recommended_outcome=None → row must be excluded from dataset
        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(
            slate.id, include_partial=True
        )
        assert rows == []


class TestDatasetFiltersWrongHash:
    """Predictions for a different composition_hash are not included."""

    def test_wrong_hash_prediction_excluded(self, db: Session):
        slate = _make_slate(db, "PG-HASH-1", n=1)
        match_id = slate.matches[0].match_id
        src = _source(db, "hash-src")
        _add_result(db, match_id, src.id, result_code="1")

        # Prediction has the right slate_id but wrong hash
        _add_prediction(db, match_id, slate.id, "deadbeef-wrong-hash")

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(
            slate.id, include_partial=True
        )
        # Scoring service will not find a prediction with slate's hash,
        # so recommended_outcome=None → row excluded
        assert rows == []


class TestDatasetMetrics:
    """Rows carry correct brier_score and hit values."""

    def test_rows_have_brier_and_hit(self, db: Session):
        slate = _make_slate(db, "PG-METRICS-1", n=2)
        match_ids = [sm.match_id for sm in slate.matches]
        src = _source(db, "metrics-src")

        # match 0: predicted "1", result "1" → hit=True
        _add_result(db, match_ids[0], src.id, result_code="1")
        _add_prediction(
            db, match_ids[0], slate.id, slate.composition_hash,
            recommended_outcome="1", home_p=0.7, draw_p=0.2, away_p=0.1,
        )
        # match 1: predicted "1", result "2" → hit=False
        _add_result(db, match_ids[1], src.id, result_code="2")
        _add_prediction(
            db, match_ids[1], slate.id, slate.composition_hash,
            recommended_outcome="1", home_p=0.7, draw_p=0.2, away_p=0.1,
        )

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert len(rows) == 2
        by_match = {r.match_id: r for r in rows}

        r0 = by_match[match_ids[0]]
        assert r0.hit is True
        assert r0.brier_score is not None
        assert r0.brier_score < 0.5  # near-perfect prediction

        r1 = by_match[match_ids[1]]
        assert r1.hit is False
        assert r1.brier_score is not None
        assert r1.brier_score > r0.brier_score


class TestDatasetTicketPicks:
    """When a valid snapshot exists, ticket picks are included in rows."""

    def test_rows_have_ticket_picks_when_snapshot_valid(self, db: Session):
        slate = _make_slate(db, "PG-TICKET-1", n=2)
        match_ids = [sm.match_id for sm in slate.matches]
        src = _source(db, "ticket-src")

        for mid in match_ids:
            _add_result(db, mid, src.id, result_code="1")
            _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")

        snap = TicketRecommendationSnapshotModel(
            slate_id=slate.id,
            composition_hash=slate.composition_hash,
            model_version="v1",
            payload_json=_snapshot_payload(slate.id, match_ids),
            is_valid=True,
            generated_at=datetime.now(timezone.utc),
        )
        db.add(snap)
        db.flush()

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert len(rows) == 2
        for row in rows:
            assert row.ticket_pick_simple == ["1"]
            assert row.ticket_pick_doubles == ["1", "X"]
            assert row.ticket_pick_full == ["1", "X", "2"]
            # Result "1" is in picks for all modes → all hits True
            assert row.ticket_hit_simple is True
            assert row.ticket_hit_doubles is True
            assert row.ticket_hit_full is True

    def test_rows_have_no_ticket_picks_without_snapshot(self, db: Session):
        slate = _make_slate(db, "PG-NOTICKET-1", n=1)
        match_id = slate.matches[0].match_id
        src = _source(db, "noticket-src")

        _add_result(db, match_id, src.id, result_code="1")
        _add_prediction(db, match_id, slate.id, slate.composition_hash)

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert len(rows) == 1
        assert rows[0].ticket_pick_simple is None
        assert rows[0].ticket_hit_simple is None


class TestPG2336WithoutResults:
    """A slate without results produces an empty dataset and a valid summary."""

    def test_empty_rows_when_no_results(self, db: Session):
        slate = _make_slate(db, "PG-2336-LIKE", n=14)
        match_ids = [sm.match_id for sm in slate.matches]

        for mid in match_ids:
            _add_prediction(db, mid, slate.id, slate.composition_hash)

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert rows == []

    def test_include_partial_still_empty_when_no_results(self, db: Session):
        slate = _make_slate(db, "PG-2336-PARTIAL", n=14)
        for sm in slate.matches:
            _add_prediction(db, sm.match_id, slate.id, slate.composition_hash)

        _score_slate(db, slate)

        rows = AdaptiveDatasetService(db).build_rows_for_slate(
            slate.id, include_partial=True
        )
        # No results recorded → all detail.result_code are None → no rows
        assert rows == []

    def test_summary_is_valid_when_no_results(self, db: Session):
        slate = _make_slate(db, "PG-2336-SUM", n=3)
        for sm in slate.matches:
            _add_prediction(db, sm.match_id, slate.id, slate.composition_hash)

        _score_slate(db, slate)

        summary = AdaptiveDatasetService(db).build_summary()
        assert summary.total_slates_scored >= 1
        assert summary.total_rows == 0
        assert summary.hit_rate is None
        assert summary.brier_score_avg is None


class TestCompleteJornada:
    """All matches scored → N trainable rows."""

    def test_complete_jornada_produces_all_rows(self, db: Session):
        n = 5
        slate = _make_slate(db, "PG-COMP-1", n=n)
        match_ids = [sm.match_id for sm in slate.matches]
        src = _source(db, "comp-src")

        for mid in match_ids:
            _add_result(db, mid, src.id, result_code="1")
            _add_prediction(db, mid, slate.id, slate.composition_hash, recommended_outcome="1")

        score = _score_slate(db, slate)
        assert score.is_complete is True

        rows = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert len(rows) == n
        assert all(r.actual_result == "1" for r in rows)
        assert all(r.hit is True for r in rows)
        assert all(r.result_is_canonical is True for r in rows)


class TestIncludePartial:
    """include_partial=False (default) skips incomplete jornadas."""

    def test_default_excludes_incomplete_jornada(self, db: Session):
        slate = _make_slate(db, "PG-PARTIAL-1", n=3)
        match_ids = [sm.match_id for sm in slate.matches]
        src = _source(db, "partial-src")

        # Only one of three matches has a result
        _add_result(db, match_ids[0], src.id, result_code="1")
        for mid in match_ids:
            _add_prediction(db, mid, slate.id, slate.composition_hash)

        score = _score_slate(db, slate)
        assert score.is_complete is False

        rows_default = AdaptiveDatasetService(db).build_rows_for_slate(slate.id)
        assert rows_default == []

        rows_partial = AdaptiveDatasetService(db).build_rows_for_slate(
            slate.id, include_partial=True
        )
        assert len(rows_partial) == 1


class TestBuildSummaryAggregates:
    """build_summary spans multiple slates."""

    def test_summary_aggregates_two_slates(self, db: Session):
        src = _source(db, "agg-src")

        # Slate A: 2 matches, all correct
        slate_a = _make_slate(db, "PG-AGG-A", n=2)
        for sm in slate_a.matches:
            _add_result(db, sm.match_id, src.id, result_code="1")
            _add_prediction(db, sm.match_id, slate_a.id, slate_a.composition_hash,
                            recommended_outcome="1", confidence_band="high")
        _score_slate(db, slate_a)

        # Slate B: 2 matches, all wrong
        slate_b = _make_slate(db, "PG-AGG-B", n=2)
        for sm in slate_b.matches:
            _add_result(db, sm.match_id, src.id, result_code="2")
            _add_prediction(db, sm.match_id, slate_b.id, slate_b.composition_hash,
                            recommended_outcome="1", confidence_band="high")
        _score_slate(db, slate_b)

        summary = AdaptiveDatasetService(db).build_summary()
        assert summary.total_slates_scored >= 2
        assert summary.total_rows >= 4
        assert summary.hit_rate is not None
        assert 0.0 <= summary.hit_rate <= 1.0
        assert summary.brier_score_avg is not None
        # high-band stats should have ≥ 4 entries
        high = summary.by_confidence_band.get("high")
        assert high is not None
        assert high.total >= 4
