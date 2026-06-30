"""R6.1 — operate_money_mode CLI orchestration (read-only, no writes)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
    ProgolSlateModel,
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


def _activate(session):
    slate = session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()
    slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
    session.commit()


def test_active_upcoming_human_output(db, monkeypatch, capsys):  # noqa: F811
    from app.db import session as db_mod
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    _activate(db)

    before = _counts(db_mod.SessionLocal)
    rc = main(["--active-upcoming"])
    after = _counts(db_mod.SessionLocal)

    assert rc == 0
    assert after == before  # 4 — no writes
    out = capsys.readouterr().out
    assert "OPERATIONAL MONEY MODE" in out
    assert DRAW in out  # 1 — slate detected
    assert "DECISION" in out
    assert "COUNTS_DELTA : ZERO" in out
    assert "WRITE_SAFETY" in out


def test_active_upcoming_json_valid_and_delta_zero(db, monkeypatch, capsys):  # noqa: F811
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    _activate(db)

    rc = main(["--active-upcoming", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)  # 2 — valid JSON
    assert payload["mode"] == "money_mode_operational_run"
    assert payload["counts_delta_zero"] is True  # 4
    assert payload["write_safety"]["audit_passed"] is True
    assert any(s["status"]["draw_code"] == DRAW for s in payload["slates"])


def test_markdown_report_written(db, monkeypatch, tmp_path, capsys):  # noqa: F811
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    _activate(db)

    out_path = tmp_path / "operate_money_mode.md"
    rc = main(["--active-upcoming", "--markdown", str(out_path)])
    assert rc == 0
    assert out_path.exists()  # 3 — markdown generated
    text = out_path.read_text(encoding="utf-8")
    assert "Operational Money Mode" in text
    assert DRAW in text
    assert "Counts before/after" in text


def test_draw_code_scope(db, monkeypatch, capsys):  # noqa: F811
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    rc = main(["--draw-code", DRAW])
    assert rc == 0
    out = capsys.readouterr().out
    assert DRAW in out
    assert "DECISION" in out


def test_unknown_draw_code_returns_error(db, monkeypatch, capsys):  # noqa: F811
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    rc = main(["--draw-code", "PG-DOES-NOT-EXIST"])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().out


def test_default_run_does_not_check_results_provider(db, monkeypatch, capsys):  # noqa: F811
    """10 — default run includes readiness but does NOT call the provider."""
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    rc = main(["--draw-code", DRAW, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results_provider_status"]["checked"] is False
    assert "readiness_expansion_summary" in payload
    assert "performance_note" in payload


def test_with_results_provider_flag_attaches_status(db, monkeypatch, capsys):  # noqa: F811
    """11 — --with-results-provider attaches the (disabled, write-free) status."""
    from app.core import settings as settings_module
    from scripts.operate_money_mode import main

    enable_canary(monkeypatch)
    monkeypatch.setattr(settings_module.settings, "results_provider_enabled", False)
    seed_canary_slate(db)

    rc = main(["--draw-code", DRAW, "--with-results-provider", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results_provider_status"]["checked"] is True
    assert payload["slates"][0]["results_provider"]["status"] == "disabled"
