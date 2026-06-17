"""Live results tracking: status normalization, partial/live scoring,
final-precedence, source priority, and the seguimiento dashboard.

Covers the requirements from the live-results feature:
  - per-match pending / live / final status,
  - result_code from a scoreline (1 / X / 2),
  - partial scoring never marks a slate complete; complete only when all
    matches are FINAL,
  - a live observation never overwrites a final one,
  - canonical final takes precedence over a live observation,
  - dashboard selects the 2 most recent closed + 2 open slates,
  - Weekend and Media Semana never mix,
  - empate real surfaces draw_was_covered.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.entities import MatchResultStatus
from app.models.tables import (
    PredictionModel,
    ProgolSlateProposalModel,
    SourceModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.live_result_service import LiveResultService, compute_result_code
from app.services.live_results_service import (
    LiveResultsService,
    finalize_complete_closed_slates,
)


# --------------------------------------------------------------------------
# Fixtures / seeding
# --------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'live_test.db'}")
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


def _seed_slate(
    session: Session,
    *,
    draw_code: str,
    week_type: str = "weekend",
    n: int = 14,
    closes_at: datetime,
    outcomes: list[str] | None = None,
    draw_probs: list[float] | None = None,
) -> Any:
    repo = SlateRepository(session)
    now = datetime.now(timezone.utc)
    matches = [
        MatchReferencePayload(
            position=i,
            competition=CompetitionPayload(name="International Friendlies"),
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
            week_type=week_type,
            registration_closes_at=closes_at,
            matches=matches,
        )
    )
    session.flush()
    slate_matches = sorted(slate.matches, key=lambda sm: sm.position)
    match_ids = [sm.match_id for sm in slate_matches]
    outcomes = outcomes or ["1"] * n
    draw_probs = draw_probs or [0.3] * n
    for sm, outcome, dp in zip(slate_matches, outcomes, draw_probs, strict=True):
        home_p = 0.6 - dp if outcome == "1" else 0.2
        away_p = max(0.0, 1.0 - home_p - dp)
        session.add(
            PredictionModel(
                match_id=sm.match_id,
                slate_id=slate.id,
                composition_hash=slate.composition_hash,
                slate_version=1,
                generated_at=now,
                home_probability=home_p,
                draw_probability=dp,
                away_probability=away_p,
                recommended_outcome=outcome,
                confidence_band="medium",
                anchors_json="{}",
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


def _match_ids(slate) -> list[str]:
    return [sm.match_id for sm in sorted(slate.matches, key=lambda s: s.position)]


def _make_official(session, slate):
    """Attach official LN proposal lineage so the slate classifies as
    official_real (eligible for official scoring)."""
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


# --------------------------------------------------------------------------
# Pure result_code
# --------------------------------------------------------------------------

def test_compute_result_code():
    assert compute_result_code(2, 1) == "1"
    assert compute_result_code(1, 1) == "X"
    assert compute_result_code(0, 3) == "2"
    assert compute_result_code(None, 1) is None
    assert compute_result_code(1, None) is None


# --------------------------------------------------------------------------
# Per-match status
# --------------------------------------------------------------------------

def test_live_results_pending_when_no_observation(db):
    slate = _seed_slate(db, draw_code="PG-A", n=3, closes_at=_future())
    payload = LiveResultsService(db).build_live_results(slate)
    assert payload["match_count"] == 3
    assert payload["pending_count"] == 3
    assert payload["completed_count"] == 0
    assert all(m["status"] == "scheduled" and m["is_pending"] for m in payload["matches"])
    assert payload["is_complete"] is False


def test_live_results_live_match(db):
    slate = _seed_slate(db, draw_code="PG-B", n=3, closes_at=_future())
    mid = _match_ids(slate)[0]
    src = _source(db, "live-src")
    LiveResultService(db).record_observation(
        match_id=mid, source_id=src.id, status=MatchResultStatus.LIVE,
        home_goals=1, away_goals=0, minute=55, is_final=False,
    )
    payload = LiveResultsService(db).build_live_results(slate)
    match0 = next(m for m in payload["matches"] if m["match_id"] == mid)
    assert match0["status"] == "live"
    assert match0["is_live"] is True and match0["is_final"] is False
    assert match0["result_code"] == "1" and match0["minute"] == 55
    assert payload["live_count"] == 1
    assert payload["is_complete"] is False


def test_live_results_final_match(db):
    slate = _seed_slate(db, draw_code="PG-C", n=3, closes_at=_future())
    mid = _match_ids(slate)[0]
    src = _source(db, "final-src")
    LiveResultService(db).record_observation(
        match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME,
        home_goals=2, away_goals=2, is_final=True,
    )
    payload = LiveResultsService(db).build_live_results(slate)
    match0 = next(m for m in payload["matches"] if m["match_id"] == mid)
    assert match0["status"] == "full_time"
    assert match0["is_final"] is True
    assert match0["result_code"] == "X"
    assert payload["completed_count"] == 1


# --------------------------------------------------------------------------
# Final precedence / no-overwrite / priority
# --------------------------------------------------------------------------

def test_live_does_not_overwrite_final(db):
    slate = _seed_slate(db, draw_code="PG-D", n=2, closes_at=_future())
    mid = _match_ids(slate)[0]
    src = _source(db, "src-1")
    live = LiveResultService(db)
    live.record_observation(
        match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME,
        home_goals=3, away_goals=0, is_final=True,
    )
    # A stale live poll arrives afterwards with a partial scoreline.
    live.record_observation(
        match_id=mid, source_id=src.id, status=MatchResultStatus.LIVE,
        home_goals=1, away_goals=0, minute=30, is_final=False,
    )
    status = live.status_for_matches([mid])[mid]
    assert status.is_final is True
    assert status.result_code == "1"
    assert status.home_goals == 3 and status.away_goals == 0


def test_canonical_final_takes_precedence_over_live(db):
    slate = _seed_slate(db, draw_code="PG-E", n=2, closes_at=_future())
    mid = _match_ids(slate)[0]
    final_src = _source(db, "final-src", priority=10)
    live_src = _source(db, "live-src", priority=90)
    live = LiveResultService(db)
    # Final from a high-priority source promotes to canonical match_results.
    live.record_observation(
        match_id=mid, source_id=final_src.id, status=MatchResultStatus.FULL_TIME,
        home_goals=0, away_goals=1, is_final=True,
    )
    # A different source still reports it live.
    live.record_observation(
        match_id=mid, source_id=live_src.id, status=MatchResultStatus.LIVE,
        home_goals=0, away_goals=0, minute=70, is_final=False,
    )
    status = live.status_for_matches([mid])[mid]
    assert status.is_final is True
    assert status.canonical_result_id is not None
    assert status.result_code == "2"


# --------------------------------------------------------------------------
# Scoring partial / complete
# --------------------------------------------------------------------------

def test_partial_scoring_not_complete(db):
    slate = _seed_slate(db, draw_code="PG-F", n=4, closes_at=_future(), outcomes=["1"] * 4)
    ids = _match_ids(slate)
    src = _source(db, "src")
    live = LiveResultService(db)
    # Only 2 of 4 final.
    live.record_observation(match_id=ids[0], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=0, is_final=True)
    live.record_observation(match_id=ids[1], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=0, away_goals=1, is_final=True)
    score = LiveResultsService(db).build_live_score(slate)
    assert score["evaluated_matches"] == 2
    assert score["pending_matches"] == 2
    assert score["is_complete"] is False
    assert score["simple_hits"] == 1  # match0 hit (1), match1 missed (2)
    # max possible: 1 hit so far + 2 still reachable; min: only the 1 banked.
    assert score["max_possible_hits"] == 3
    assert score["min_possible_hits"] == 1


def test_complete_scoring_only_when_all_final(db):
    slate = _seed_slate(db, draw_code="PG-G", n=3, closes_at=_future(), outcomes=["1", "1", "1"])
    ids = _match_ids(slate)
    src = _source(db, "src")
    live = LiveResultService(db)
    for i, mid in enumerate(ids):
        live.record_observation(match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=0, is_final=True)
    score = LiveResultsService(db).build_live_score(slate)
    assert score["evaluated_matches"] == 3
    assert score["is_complete"] is True
    assert score["simple_hits"] == 3
    assert score["current_hit_rate"] == 1.0
    assert score["brier_partial"] is not None


# --------------------------------------------------------------------------
# Draws
# --------------------------------------------------------------------------

def test_draw_real_and_covered(db):
    # Match ends X; doubles picks include X, so draw_was_covered is true.
    slate = _seed_slate(db, draw_code="PG-H", n=2, closes_at=_future(), outcomes=["1", "1"], draw_probs=[0.33, 0.1])
    mid = _match_ids(slate)[0]
    src = _source(db, "src")
    LiveResultService(db).record_observation(match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=1, is_final=True)
    payload = LiveResultsService(db).build_live_results(slate)
    m0 = next(m for m in payload["matches"] if m["match_id"] == mid)
    assert m0["result_code"] == "X"
    assert m0["draw_was_real"] is True
    assert m0["draw_was_covered"] is True
    assert m0["simple_hit"] is False  # simple picked "1"
    assert m0["doubles_hit"] is True  # doubles = [1, X]
    assert m0["draw_risk"]["is_strong_draw"] is True


def test_partial_draw_delta(db):
    slate = _seed_slate(db, draw_code="PG-I", n=2, closes_at=_future(), outcomes=["1", "1"], draw_probs=[0.3, 0.3])
    ids = _match_ids(slate)
    src = _source(db, "src")
    live = LiveResultService(db)
    # One final draw observed.
    live.record_observation(match_id=ids[0], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=1, is_final=True)
    score = LiveResultsService(db).build_live_score(slate)
    assert score["empates_reales_hasta_ahora"] == 1
    # expected over the single evaluated match = 0.3 → delta = 1 - 0.3 = 0.7
    assert score["draw_delta_partial"] == pytest.approx(0.7)


# --------------------------------------------------------------------------
# Worker finalize + dashboard selection
# --------------------------------------------------------------------------

def test_finalize_persists_only_complete_closed(db):
    from app.repositories.jornada_score_repository import JornadaScoreRepository

    closed = _seed_slate(db, draw_code="PG-CLOSED", n=2, closes_at=_past(), outcomes=["1", "1"])
    _make_official(db, closed)  # official lineage → eligible for scoring
    _seed_slate(db, draw_code="PG-OPEN", n=2, closes_at=_future(), outcomes=["1", "1"])
    src = _source(db, "src")
    live = LiveResultService(db)
    for mid in _match_ids(closed):
        live.record_observation(match_id=mid, source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=0, is_final=True)
    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    assert "PG-CLOSED" in summary["finalized"]
    assert "PG-OPEN" not in summary["finalized"]
    saved = JornadaScoreRepository(db).get_latest_for_slate(closed.id)
    assert saved is not None and saved.is_complete is True


def test_finalize_skips_incomplete_closed(db):
    closed = _seed_slate(db, draw_code="PG-PART", n=3, closes_at=_past(), outcomes=["1", "1", "1"])
    src = _source(db, "src")
    LiveResultService(db).record_observation(match_id=_match_ids(closed)[0], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=0, is_final=True)
    summary = finalize_complete_closed_slates(db, now=datetime.now(timezone.utc))
    assert "PG-PART" not in summary["finalized"]


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=3)


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


# --------------------------------------------------------------------------
# Endpoint + dashboard (HTTP via client fixture)
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_live_results_endpoint(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        slate = _seed_slate(session, draw_code="PG-EP", n=3, closes_at=_future(), outcomes=["1", "1", "1"])
        sid = slate.id
        mid = _match_ids(slate)[0]
        src = _source(session, "src")
        LiveResultService(session).record_observation(
            match_id=mid, source_id=src.id, status=MatchResultStatus.LIVE,
            home_goals=2, away_goals=0, minute=60, is_final=False,
        )
        session.commit()

    resp = await client.get(f"/api/slates/{sid}/live-results")
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] == 3
    assert body["live_count"] == 1
    assert body["is_complete"] is False

    score = await client.get(f"/api/slates/{sid}/live-score")
    assert score.status_code == 200
    sbody = score.json()
    assert sbody["evaluated_matches"] == 0  # nothing final yet
    assert sbody["live_matches"] == 1
    assert sbody["is_complete"] is False


@pytest.mark.anyio
async def test_live_results_endpoint_404(client):
    resp = await client.get("/api/slates/does-not-exist/live-results")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_dashboard_selects_two_closed_two_open_without_mixing(client):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        # 2 closed (weekend + midweek), 2 open (weekend + midweek), each with
        # predictions + a valid snapshot.
        _seed_slate(session, draw_code="PG-CW", week_type="weekend", n=14, closes_at=_past())
        _seed_slate(session, draw_code="PGM-CM", week_type="midweek", n=9, closes_at=_past())
        _seed_slate(session, draw_code="PG-OW", week_type="weekend", n=14, closes_at=_future())
        _seed_slate(session, draw_code="PGM-OM", week_type="midweek", n=9, closes_at=_future())
        # A closed slate WITHOUT a snapshot must be excluded from the dashboard.
        repo = SlateRepository(session)
        now = datetime.now(timezone.utc)
        repo.upsert_slate(
            ProgolSlateCreate(
                label="No snap", draw_code="PG-NOSNAP", week_type="weekend",
                registration_closes_at=_past(),
                matches=[
                    MatchReferencePayload(
                        position=i, competition=CompetitionPayload(name="X"),
                        home_team=TeamPayload(name=f"h{i}"), away_team=TeamPayload(name=f"a{i}"),
                        kickoff_at=now - timedelta(hours=2),
                    )
                    for i in range(1, 15)
                ],
            )
        )
        session.commit()

    resp = await client.get("/api/slates/live/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    closed_codes = {e["draw_code"] for e in body["closed"]}
    open_codes = {e["draw_code"] for e in body["open"]}
    assert len(body["closed"]) == 2
    assert len(body["open"]) == 2
    assert closed_codes == {"PG-CW", "PGM-CM"}
    assert open_codes == {"PG-OW", "PGM-OM"}
    assert "PG-NOSNAP" not in closed_codes  # filtered: no valid snapshot
    # Weekend / MS never mixed: each entry's week_type is intact.
    by_code = {e["draw_code"]: e for e in body["closed"] + body["open"]}
    assert by_code["PG-CW"]["week_type"] == "weekend"
    assert by_code["PGM-CM"]["week_type"] == "midweek"
    assert by_code["PG-OW"]["status_label"] == "Abierta"
    assert by_code["PG-CW"]["status_label"] in {"Cerrada", "Completa"}
