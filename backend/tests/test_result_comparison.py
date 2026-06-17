"""Postmortem result-comparison: original-snapshot guarantee + diagnosis.

Locks the critical product rule: the comparison always scores against the
ORIGINAL pre-close ticket, never a snapshot generated after results were
known. Also covers the per-match diagnosis classes, the empate-cubierta
read, and the no-results empty state.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.domain.entities import MatchResultStatus
from app.models.tables import TicketRecommendationSnapshotModel
from app.services.live_result_service import LiveResultService
from app.services.live_results_service import LiveResultsService
from tests.test_live_results import _seed_slate, _match_ids, _source, _past, _future  # noqa: E402


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'cmp.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _add_snapshot(session, slate, *, generated_at, simple_pick, is_valid=True):
    """Add a valid snapshot whose simple pick for every match is `simple_pick`."""
    recs = [
        {
            "match_id": sm.match_id,
            "position": sm.position,
            "decisions": {
                "simple": {"pick_type": "fixed", "picks": [simple_pick]},
                "doubles": {"pick_type": "double", "picks": [simple_pick, "X"]},
                "full": {"pick_type": "triple", "picks": ["1", "X", "2"]},
            },
        }
        for sm in sorted(slate.matches, key=lambda s: s.position)
    ]
    snap = TicketRecommendationSnapshotModel(
        slate_id=slate.id,
        model_version="ticket-optimizer-v2",
        payload_json=json.dumps({"slate_id": slate.id, "recommendations": recs}),
        composition_hash=slate.composition_hash,
        is_valid=is_valid,
        generated_at=generated_at,
    )
    session.add(snap)
    session.flush()
    return snap


def test_comparison_uses_original_pre_close_snapshot(db):
    closes = datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)
    slate = _seed_slate(db, draw_code="PGM-799", week_type="midweek", n=3,
                        closes_at=closes, outcomes=["1", "1", "1"])
    # Original ticket (pre-close) picks "1"; a later ticket (post-close,
    # after results) picks "2". The comparison must use the pre-close one.
    original = _add_snapshot(db, slate, generated_at=closes - timedelta(hours=2), simple_pick="1")
    _add_snapshot(db, slate, generated_at=closes + timedelta(days=1), simple_pick="2")
    db.commit()

    src = _source(db, "LN")
    LiveResultService(db).record_observation(
        match_id=_match_ids(slate)[0], source_id=src.id,
        status=MatchResultStatus.FULL_TIME, home_goals=2, away_goals=0, is_final=True,
    )
    db.commit()

    comp = LiveResultsService(db).build_result_comparison(slate)
    assert comp["original_snapshot"]["snapshot_id"] == original.id
    pos1 = comp["matches"][0]
    # Original simple pick was "1"; result "1" → hit. (Post-close "2" would miss.)
    assert pos1["simple_hit"] is True
    assert pos1["diagnosis"] == "acierto"


def test_comparison_diagnoses(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=4, closes_at=_past(),
                        outcomes=["1", "1", "1", "1"], draw_probs=[0.3, 0.3, 0.3, 0.3])
    ids = _match_ids(slate)
    src = _source(db, "LN")
    live = LiveResultService(db)
    live.record_observation(match_id=ids[0], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=2, away_goals=0, is_final=True)  # pred 1, real 1 → acierto
    live.record_observation(match_id=ids[1], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=1, is_final=True)  # pred 1, real X → fallo por empate
    live.record_observation(match_id=ids[2], source_id=src.id, status=MatchResultStatus.FULL_TIME, home_goals=0, away_goals=2, is_final=True)  # pred 1, real 2 → fallo (salió visitante)
    # ids[3] left pending
    db.commit()

    comp = LiveResultsService(db).build_result_comparison(slate)
    diag = {m["position"]: m["diagnosis"] for m in comp["matches"]}
    assert diag[1] == "acierto"
    assert diag[2] == "fallo por empate"
    assert diag[3] == "fallo (salió visitante)"
    assert diag[4] == "pendiente"
    assert comp["is_complete"] is False


def test_comparison_empate_cubierta(db):
    slate = _seed_slate(db, draw_code="PG-2336", n=1, closes_at=_past(),
                        outcomes=["1"], draw_probs=[0.33])
    src = _source(db, "LN")
    LiveResultService(db).record_observation(
        match_id=_match_ids(slate)[0], source_id=src.id,
        status=MatchResultStatus.FULL_TIME, home_goals=1, away_goals=1, is_final=True,
    )
    db.commit()
    comp = LiveResultsService(db).build_result_comparison(slate)
    m0 = comp["matches"][0]
    assert m0["draw_was_real"] is True
    assert m0["draw_was_covered"] is True   # seeded doubles = [pick, X]
    assert m0["simple_hit"] is False
    assert m0["doubles_hit"] is True
    assert m0["diagnosis"] == "fallo por empate"


def test_comparison_empty_when_no_results(db):
    slate = _seed_slate(db, draw_code="PG-2337", n=14, closes_at=_future())
    comp = LiveResultsService(db).build_result_comparison(slate)
    assert comp["results_ingested"] is False
    assert comp["completed_count"] == 0
    assert comp["is_complete"] is False
    assert all(m["diagnosis"] == "pendiente" for m in comp["matches"])
    # Original snapshot still resolved (seeded valid snapshot).
    assert comp["original_snapshot"]["snapshot_id"] is not None
