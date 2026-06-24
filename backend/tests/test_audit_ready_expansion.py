"""R6.3 — readiness expansion audit (read-only, no false READY)."""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models.tables import MatchResultModel, PredictionModel, ProgolSlateModel
from app.services.readiness_expansion_service import build_ready_expansion
from backend.tests.test_ticket_canary_dry_run_service import (
    DRAW,
    db,  # noqa: F401 — pytest fixture
    enable_canary,
    seed_canary_slate,
)


def _slate(session):
    return session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()


def _counts(session_factory):
    with session_factory() as s:
        return (
            int(s.scalar(select(func.count()).select_from(MatchResultModel)) or 0),
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
        )


def test_audit_explains_blockers_and_no_writes(db, monkeypatch):  # noqa: F811
    """8 + 6 + 7 — every NOT_READY match has blockers + improvements; no writes."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    before = _counts(db_mod.SessionLocal)
    report = build_ready_expansion(db, _slate(db))
    after = _counts(db_mod.SessionLocal)

    assert after == before
    assert report["mode"] == "readiness_expansion_audit"
    assert report["write_safety"]["writes_performed"] is False
    for m in report["matches"]:
        if m["current_status"] == "NOT_READY":
            assert m["blocked_by"], f"pos {m['position']} must explain its blockers"
            assert m["can_be_improved_by"]


def test_audit_does_not_invent_ready(db, monkeypatch):  # noqa: F811
    """9 — a match is only safe_to_promote when already defensible (no false READY)."""
    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_ready_expansion(db, _slate(db))
    for m in report["matches"]:
        if not m["simple_allowed"] if "simple_allowed" in m else (m["current_status"] == "NOT_READY"):
            assert m["safe_to_promote_now"] is False
    # safe_promotions must be a subset of READY positions only.
    ready_positions = {m["position"] for m in report["matches"] if m["current_status"] == "READY"}
    assert set(report["safe_promotions"]).issubset(ready_positions)


def test_audit_json_serialisable(db, monkeypatch):  # noqa: F811
    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_ready_expansion(db, _slate(db))
    json.dumps(report)  # must not raise
    assert "no_promote_reason" in report
