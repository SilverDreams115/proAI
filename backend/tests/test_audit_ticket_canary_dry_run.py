"""R5.7 — ticket canary dry-run CLI auditor (read-only, valid JSON)."""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
    TicketRecommendationSnapshotModel,
)
from backend.tests.test_ticket_canary_dry_run_service import (
    DRAW,
    db,  # noqa: F401 — pytest fixture
    enable_canary,
    seed_canary_slate,
)


def _counts(session_factory):
    with session_factory() as s:
        return (
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
            int(s.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0),
            int(s.scalar(select(func.count()).select_from(TicketRecommendationSnapshotModel)) or 0),
        )


def test_cli_json_output_and_no_writes(db, monkeypatch, capsys):  # noqa: F811
    from app.db import session as db_mod
    from scripts.audit_ticket_canary_dry_run import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    before = _counts(db_mod.SessionLocal)
    rc = main(["--draw-code", DRAW, "--json"])
    assert rc == 0
    after = _counts(db_mod.SessionLocal)
    assert after == before

    out = capsys.readouterr().out
    payload = json.loads(out)  # must be valid JSON
    assert payload["mode"] == "ticket_canary_dry_run"
    assert payload["slate"]["draw_code"] == DRAW
    assert payload["write_safety"]["writes_performed"] is False


def test_cli_human_output(db, monkeypatch, capsys):  # noqa: F811
    from scripts.audit_ticket_canary_dry_run import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    rc = main(["--draw-code", DRAW])
    assert rc == 0
    out = capsys.readouterr().out
    assert DRAW in out
    assert "current" in out and "canary" in out
    assert "write_safety" in out
