"""R6.3 — free results provider service (read-only dry-run, no writes)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.core import settings as settings_module
from app.models.tables import MatchResultModel, PredictionModel, ProgolSlateModel
from app.services.results_provider_service import (
    ProviderMatch,
    STATUS_DISABLED,
    STATUS_INSUFFICIENT,
    STATUS_MISSING_KEY,
    STATUS_OK,
    _match_one,
    build_slate_results_dry_run,
    match_slate,
    probe_provider,
    provider_configured,
)
from backend.tests.test_ticket_canary_dry_run_service import (
    DRAW,
    db,  # noqa: F401 — pytest fixture
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


def test_disabled_provider_makes_no_writes(db, monkeypatch):  # noqa: F811
    """2 + 6 + 7 — disabled provider returns a status and writes nothing."""
    from app.db import session as db_mod

    monkeypatch.setattr(settings_module.settings, "results_provider_enabled", False)
    seed_canary_slate(db)

    before = _counts(db_mod.SessionLocal)
    report = build_slate_results_dry_run(_slate(db))
    after = _counts(db_mod.SessionLocal)

    assert after == before
    assert report["status"] == STATUS_DISABLED
    assert report["write_safety"]["writes_performed"] is False
    assert report["coverage"]["matched"] == 0


def test_missing_api_key_is_non_fatal(db, monkeypatch):  # noqa: F811
    """1 — enabled but no key -> missing_key status, never an exception."""
    monkeypatch.setattr(settings_module.settings, "results_provider_enabled", True)
    monkeypatch.setattr(settings_module.settings, "football_data_api_key", None)
    seed_canary_slate(db)

    report = build_slate_results_dry_run(_slate(db))
    assert report["status"] == STATUS_MISSING_KEY
    assert report["write_safety"]["writes_performed"] is False
    assert provider_configured("football_data_org") is False


def test_injected_provider_data_yields_coverage(db, monkeypatch):  # noqa: F811
    """3 + 5 — with provider data the matcher resolves aliased names; no writes."""
    monkeypatch.setattr(settings_module.settings, "results_provider_enabled", True)
    monkeypatch.setattr(settings_module.settings, "football_data_api_key", "test-key")
    seed_canary_slate(db)

    # Position 2 of the seed slate is "Czech Republic vs Mexico". Provider uses
    # the Spanish/aliased forms — must still resolve via NormalizationService.
    fake = [ProviderMatch("Chequia", "México", "finished", "1-0", "2026-01-02", "Friendly")]
    report = build_slate_results_dry_run(_slate(db), fetch_fn=lambda f, t: fake)
    assert report["status"] in (STATUS_OK, STATUS_INSUFFICIENT)
    assert report["write_safety"]["writes_performed"] is False
    pos2 = next(m for m in report["matches"] if m["position"] == 2)
    assert pos2["confidence"] == "high"
    assert report["coverage"]["matched"] >= 1


def test_slate_window_covers_the_playing_week_despite_synthetic_kickoffs(db):  # noqa: F811
    """Kickoffs can be synthetic placeholders clustered near the cierre (LN's
    guide carries no times), so a ±1d window missed real fixtures — PGM-804's
    World Cup semifinals fell outside it and coverage was zero. The window must
    extend past max(kickoff, cierre) by a margin that covers the concurso's
    actual playing week."""
    from datetime import datetime, timezone

    from app.services.results_provider_service import _slate_window

    seed_canary_slate(db)
    slate = _slate(db)
    # Synthetic kickoffs are Jan 1-3; the (provisional) cierre lands Jan 5.
    slate.registration_closes_at = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)
    db.flush()
    date_from, date_to = _slate_window(slate)
    assert date_from == "2025-12-31"
    # max(kickoff Jan 3, cierre Jan 5) + 3d margin => Jan 8: a real match played
    # 2-3 days after the last synthetic kickoff still falls inside the window.
    assert date_to == "2026-01-08"


def test_matcher_normalizes_aliases():
    """5 — México/Mexico, E.U.A./USA, Chequia/Czech Republic all resolve."""
    pm_mx = [ProviderMatch("México", "Canadá", "finished", "2-1", None)]
    match, conf = _match_one("Mexico", "Canada", pm_mx)
    assert conf == "high" and match is not None

    pm_usa = [ProviderMatch("E.U.A.", "Turquía", "finished", "0-0", None)]
    match, conf = _match_one("USA", "Turkey", pm_usa)
    assert conf == "high"

    pm_cz = [ProviderMatch("Chequia", "España", "finished", "1-1", None)]
    match, conf = _match_one("Czech Republic", "Spain", pm_cz)
    assert conf == "high"


def test_match_slate_unmatched_when_no_provider_data(db):  # noqa: F811
    seed_canary_slate(db)
    cov = match_slate(_slate(db), [])
    assert cov["matched"] == 0
    assert all(row["confidence"] == "none" for row in cov["rows"])


def test_probe_thesportsdb_is_cross_check_only():
    assert probe_provider("thesportsdb")["status"] == "cross_check_only"
