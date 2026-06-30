"""R6.3 — probe_free_results_source CLI (read-only)."""
from __future__ import annotations

import json

from app.core import settings as settings_module
from backend.tests.test_ticket_canary_dry_run_service import (
    DRAW,
    db,  # noqa: F401 — pytest fixture
    seed_canary_slate,
)


def test_probe_missing_key_non_fatal(db, monkeypatch, capsys):  # noqa: F811
    """1 — probe with no key reports a status and exits 0 (non-fatal)."""
    from scripts.probe_free_results_source import main

    monkeypatch.setattr(settings_module.settings, "football_data_api_key", None)
    seed_canary_slate(db)

    rc = main(["--provider", "football_data_org", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["probe"]["status"] == "unavailable_missing_key"
    assert payload["probe"]["api_key_present"] is False


def test_probe_draw_code_coverage(db, monkeypatch, capsys):  # noqa: F811
    from scripts.probe_free_results_source import main

    monkeypatch.setattr(settings_module.settings, "results_provider_enabled", False)
    monkeypatch.setattr(settings_module.settings, "football_data_api_key", None)
    seed_canary_slate(db)

    rc = main(["--provider", "football_data_org", "--draw-code", DRAW])
    assert rc == 0
    out = capsys.readouterr().out
    assert DRAW in out
    assert "coverage" in out


def test_probe_thesportsdb_cross_check(db, monkeypatch, capsys):  # noqa: F811
    from scripts.probe_free_results_source import main

    seed_canary_slate(db)
    rc = main(["--provider", "thesportsdb", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["probe"]["status"] == "cross_check_only"
