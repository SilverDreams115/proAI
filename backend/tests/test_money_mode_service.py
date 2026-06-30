"""R6.0 — Money Mode service: read-only play/don't-play + 3 tickets, no writes."""
from __future__ import annotations

from math import prod

from sqlalchemy import func, select

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)

# Reuse the canary slate seed + canary toggle from the dry-run test module.
from backend.tests.test_ticket_canary_dry_run_service import (  # noqa: E402
    DRAW,
    db,  # noqa: F401  (pytest fixture)
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


def _slate(session):
    return session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()


def test_money_mode_structure_and_no_writes(db, monkeypatch):  # noqa: F811
    from app.db import session as db_mod
    from app.services.money_mode_service import build_money_mode

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    before = _counts(db_mod.SessionLocal)
    report = build_money_mode(db, _slate(db))
    after = _counts(db_mod.SessionLocal)

    # 9, 10, 11 — no predictions / feature snapshots / ticket snapshots written.
    assert after == before
    assert report["mode"] == "money_mode_release_candidate"
    assert report["production_active"] is False
    assert report["ticket_integration_active"] is False
    assert report["write_safety"] == {"writes_performed": False, "snapshots_created": False}

    # 4 — all three tickets present.
    assert set(report["tickets"]) == {"aggressive", "balanced", "conservative"}
    # 5 — a recommended ticket is exposed (or None when NO_JUGAR).
    rec = report["decision"]["recommended_ticket"]
    assert rec in (None, "aggressive", "balanced", "conservative")
    if rec is not None:
        assert report["tickets"][rec]["recommended"] is True

    # decision is always one of the non-ambiguous verdicts.
    assert report["decision"]["status"] in {
        "JUGAR_BALANCEADO",
        "NO_JUGAR",
        "JUGAR_SOLO_AGRESIVO",
        "JUGAR_SOLO_BALANCEADO",
        "JUGAR_SOLO_CONSERVADOR",
        "JUGAR_CON_CAUTELA",
    }


def test_no_simple_never_renders_as_simple(db, monkeypatch):  # noqa: F811
    """6 + 7 — a NO-SIMPLE position (e.g. a low-evidence friendly) is never a
    simple in any of the three tickets, and Norway vs France never goes simple."""
    from app.services.money_mode_service import build_money_mode

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_money_mode(db, _slate(db))

    blocked_positions = set(report["do_not_simple_positions"])
    for ticket in report["tickets"].values():
        for sel in ticket["selections"]:
            if sel["position"] in blocked_positions:
                assert sel["type"] != "simple"

    norway = next(m for m in report["matches"] if "Norway" in m["match"])
    assert norway["position"] in blocked_positions
    assert norway["money_mode_pick_type"] != "simple"


def test_combinations_are_product_of_pick_widths(db, monkeypatch):  # noqa: F811
    """8 — estimated_combinations == product(1 simple/no_simple, 2 double, 3 triple)."""
    from app.services.money_mode_service import build_money_mode

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_money_mode(db, _slate(db))

    width = {"simple": 1, "no_simple": 1, "double": 2, "triple": 3}
    for ticket in report["tickets"].values():
        expected = prod(width[sel["type"]] for sel in ticket["selections"])
        assert ticket["estimated_combinations"] == expected
        assert ticket["estimated_cost"] is None  # costo unitario no configurado


def test_canary_off_reports_no_canary_influence(db, monkeypatch):  # noqa: F811
    from app.core import settings as settings_module
    from app.services.money_mode_service import build_money_mode

    monkeypatch.setattr(settings_module.settings, "team_rating_canary_enabled", False)
    seed_canary_slate(db)
    report = build_money_mode(db, _slate(db))
    assert report["canary_influence_positions"] == []


def test_active_upcoming_includes_seeded_slate(db, monkeypatch):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from app.services.money_mode_service import build_active_slates_money_mode

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    # Mark the slate active/upcoming so it enters the active_upcoming scope.
    slate = _slate(db)
    slate.registration_closes_at = datetime.now(timezone.utc) + timedelta(days=3)
    db.commit()

    report = build_active_slates_money_mode(db)
    assert report["scope"] == "active_upcoming"
    assert report["slate_count"] >= 1
    draws = {s["slate"]["draw_code"] for s in report["slates"]}
    assert DRAW in draws
    assert report["write_safety"] == {"writes_performed": False, "snapshots_created": False}
