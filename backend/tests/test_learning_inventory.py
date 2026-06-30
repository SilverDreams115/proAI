"""R7.0 — completed-slate learning inventory (read-only)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.tables import MatchResultModel, PredictionModel
from app.services.completed_slate_inventory_service import build_completed_slate_inventory
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def _counts(session):
    return (
        int(session.scalar(select(func.count()).select_from(MatchResultModel)) or 0),
        int(session.scalar(select(func.count()).select_from(PredictionModel)) or 0),
    )


def test_pg2337_pending_appears_in_inventory(learn_db):  # noqa: F811
    """1 — a finished slate without results shows as closed_pending_results."""
    seed_official_slate(learn_db, draw="PG-2337", n=14, with_results=False)
    report = build_completed_slate_inventory(learn_db)
    item = next(s for s in report["slates"] if s["draw_code"] == "PG-2337")
    assert item["state"] == "closed_pending_results"
    assert item["comparable"] is False
    assert item["prediction_count"] == 14
    assert item["canonical_result_count"] == 0


def test_pgm800_pending_appears_in_inventory(learn_db):  # noqa: F811
    """2 — PGM-800 appears and is correctly blocked without results."""
    seed_official_slate(learn_db, draw="PGM-800", n=9, week_type="midweek", with_results=False)
    report = build_completed_slate_inventory(learn_db)
    codes = {s["draw_code"] for s in report["slates"]}
    assert "PGM-800" in codes
    item = next(s for s in report["slates"] if s["draw_code"] == "PGM-800")
    assert item["comparable"] is False
    assert "missing_local_results" in item["blockers"] or item["state"] == "closed_pending_results"


def test_comparable_slate_marked_comparable(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-DONE", n=4, with_results=True)
    report = build_completed_slate_inventory(learn_db)
    item = next(s for s in report["slates"] if s["draw_code"] == "PG-DONE")
    assert item["state"] == "closed_comparable"
    assert item["comparable"] is True
    assert item["blockers"] == []


def test_conflict_slate_marked_conflict(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-CONF", n=4, with_results=True, conflict_pos=2)
    report = build_completed_slate_inventory(learn_db)
    item = next(s for s in report["slates"] if s["draw_code"] == "PG-CONF")
    assert item["state"] == "closed_conflict"
    assert item["comparable"] is False


def test_inventory_is_read_only(learn_db):  # noqa: F811
    """15 — building the inventory writes nothing."""
    seed_official_slate(learn_db, draw="PG-2337", n=14, with_results=False)
    before = _counts(learn_db)
    build_completed_slate_inventory(learn_db)
    assert _counts(learn_db) == before
