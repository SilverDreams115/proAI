"""R6.4 — slate options service (always-present, respects Money Mode, no writes)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.tables import (
    MatchFeatureSnapshotModel,
    PredictionModel,
    ProgolSlateModel,
    TicketRecommendationSnapshotModel,
)
from app.services.slate_options_service import build_slate_options
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
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
            int(s.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0),
            int(s.scalar(select(func.count()).select_from(TicketRecommendationSnapshotModel)) or 0),
        )


def test_options_always_present_and_no_writes(db, monkeypatch):  # noqa: F811
    """6 + 11 — always returns the 3 named options (+manual); writes nothing."""
    from app.db import session as db_mod

    enable_canary(monkeypatch)
    seed_canary_slate(db)

    before = _counts(db_mod.SessionLocal)
    report = build_slate_options(db, _slate(db))
    after = _counts(db_mod.SessionLocal)

    assert after == before
    names = [o["name"] for o in report["options"]]
    assert "Agresiva" in names
    assert "Balanceada" in names
    assert "Conservadora" in names
    assert any("Manual" in n for n in names)
    assert report["write_safety"]["writes_performed"] is False


def test_no_jugar_marks_no_option_recommended(db, monkeypatch):  # noqa: F811
    """7 — when NO_JUGAR, no option is recommended and action is NO_COMPRAR."""
    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_slate_options(db, _slate(db))

    # The seed (friendlies, low evidence) resolves to NO_JUGAR.
    assert report["money_mode_decision"] == "NO_JUGAR"
    assert report["recommended_action"] == "NO_COMPRAR"
    assert all(o["recommended"] is False for o in report["options"])
    assert all(o["playable"] is False for o in report["options"])


def test_options_carry_pricing_unverified(db, monkeypatch):  # noqa: F811
    """Options carry combinations + unverified cost (None, not $0)."""
    enable_canary(monkeypatch)
    seed_canary_slate(db)
    report = build_slate_options(db, _slate(db))
    for opt in report["options"]:
        assert opt["combinations"] == (2 ** opt["double_count"]) * (3 ** opt["triple_count"])
        assert opt["price_status"] == "unverified"
        assert opt["estimated_cost"] is None
    assert report["pricing_verified"] is False
