"""R6.4 — options + tracking validation endpoints + guarded apply (read-only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import MatchResultModel, ProgolSlateModel
from backend.tests.test_ticket_canary_dry_run_service import DRAW, enable_canary, seed_canary_slate


def _result_count(engine):
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(MatchResultModel)) or 0)


@pytest.mark.anyio
async def test_slate_options_endpoint(client, monkeypatch):
    """8 — PG-2338/PGM-801-style slate options endpoint responds."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    resp = await client.get(f"/api/predictions/slates/{slate_id}/options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "slate_options"
    names = [o["name"] for o in body["options"]]
    assert "Agresiva" in names and "Balanceada" in names and "Conservadora" in names
    # NO_JUGAR -> nothing recommended
    assert all(o["recommended"] is False for o in body["options"])


@pytest.mark.anyio
async def test_active_slates_options_endpoint(client, monkeypatch):
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    with Session(db_mod.engine) as session:
        slate = seed_canary_slate(session)
        slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.commit()

    resp = await client.get("/api/predictions/active-slates/options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "slate_options_active_upcoming"
    assert any(s["draw_code"] == DRAW for s in body["slates"])


@pytest.mark.anyio
async def test_results_validation_endpoints(client):
    """9/10 — per-slate and all-completed validation endpoints respond read-only."""
    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        seed_canary_slate(session)
        slate_id = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one().id

    before = _result_count(db_mod.engine)
    resp = await client.get(f"/api/tracking/slates/{slate_id}/results-validation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "completed_slate_results_validation"
    assert body["write_safety"]["writes_performed"] is False

    resp_all = await client.get("/api/tracking/completed-slates/results-validation")
    assert resp_all.status_code == 200
    assert resp_all.json()["mode"] == "completed_slate_results_validation_all"
    assert _result_count(db_mod.engine) == before  # 11 — no writes


@pytest.mark.anyio
async def test_results_validation_404(client):
    resp = await client.get("/api/tracking/slates/does-not-exist/results-validation")
    assert resp.status_code == 404


def test_apply_completed_results_blocked_without_confirm(db, monkeypatch):  # noqa: F811
    """12 — apply is blocked without the typed confirmation."""
    from scripts.apply_completed_slate_results import main

    seed_canary_slate(db)
    rc = main(["--draw-code", DRAW])  # no --apply/--confirm
    assert rc == 2  # BLOCKED


def test_apply_completed_results_not_ready_with_confirm(db, monkeypatch):  # noqa: F811
    """12 — even with the token, an unready slate is refused (no write)."""
    from app.db import session as db_mod
    from scripts.apply_completed_slate_results import main

    seed_canary_slate(db)
    before = _result_count(db_mod.engine)
    rc = main(["--draw-code", DRAW, "--apply", "--confirm", "APPLY-COMPLETED-SLATE-RESULTS"])
    assert rc == 4  # NOT READY (coverage 0)
    assert _result_count(db_mod.engine) == before


# pull in the db fixture for the two CLI tests
from backend.tests.test_ticket_canary_dry_run_service import db  # noqa: E402,F401
