"""GET /api/slates/visible — selector is never empty when official slates exist.

Pins the fallback contract surfaced by the "UI sin boletas" fix:
  * open official slates are returned first (reason=open_slate);
  * when none are open, the most recent official slates with a prediction +
    valid snapshot are returned read-only (reason=fallback_recent);
  * demo / non-official slates are excluded from both lists;
  * selected_default_slate uses an open slate first, a recent one otherwise;
  * Weekend and Media Semana stay separate (each entry keeps its week_type);
  * discovery reports the latest observation per week_type.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session

from app.api.routes.slates import visible_slates
from app.domain.entities import MatchResultStatus
from app.models.tables import ProgolSlateProposalModel
from app.services.live_result_service import LiveResultService

from tests.test_live_results import (  # noqa: E402
    _future,
    _make_official,
    _past,
    _seed_slate,
    _source,
)


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'visible.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _visible(db, *, limit_recent=4):
    # Called directly (not via FastAPI), so pass a concrete limit rather than
    # the Query default object.
    return asyncio.run(visible_slates(limit_recent=limit_recent, session=db))


def test_open_official_slate_is_returned_first(db):
    slate = _seed_slate(db, draw_code="PG-OPEN", n=14, closes_at=_future())
    _make_official(db, slate)
    res = _visible(db)
    assert res.reason == "open_slate"
    assert res.selected_default_slate_id == slate.id
    assert [s.draw_code for s in res.open_slates] == ["PG-OPEN"]
    assert res.recent_slates == []
    assert res.open_slates[0].read_only is False


def test_fallback_to_recent_when_no_open(db):
    slate = _seed_slate(db, draw_code="PG-2338", n=14, closes_at=_past())
    _make_official(db, slate)
    res = _visible(db)
    assert res.reason == "fallback_recent"
    assert res.selected_default_slate_id == slate.id
    assert res.open_slates == []
    assert [s.draw_code for s in res.recent_slates] == ["PG-2338"]
    # Closed official slate is read-only.
    assert res.recent_slates[0].read_only is True


def test_open_preferred_over_recent(db):
    open_slate = _seed_slate(db, draw_code="PGM-OPEN", week_type="midweek", n=14, closes_at=_future())
    _make_official(db, open_slate)
    closed_slate = _seed_slate(db, draw_code="PG-2337", week_type="weekend", n=14, closes_at=_past())
    _make_official(db, closed_slate)
    res = _visible(db)
    assert res.reason == "open_slate"
    assert res.selected_default_slate_id == open_slate.id


def test_demo_slate_excluded(db):
    # _seed_slate with no official proposal => synthetic_demo (International
    # Friendlies only) => excluded from both lists.
    _seed_slate(db, draw_code="PG-DEMO", n=14, closes_at=_past())
    res = _visible(db)
    assert res.open_slates == []
    assert res.recent_slates == []
    assert res.selected_default_slate_id is None
    assert res.reason == "no_official_slates"


def test_recent_requires_predictions_and_snapshot(db):
    # _seed_slate always seeds predictions + a valid snapshot, so an official
    # closed slate qualifies; a non-official one never reaches the check.
    slate = _seed_slate(db, draw_code="PG-2336", n=14, closes_at=_past())
    _make_official(db, slate)
    res = _visible(db)
    assert [s.draw_code for s in res.recent_slates] == ["PG-2336"]


def test_recent_limit(db):
    for i, code in enumerate(["PG-A", "PG-B", "PG-C", "PG-D", "PG-E"]):
        s = _seed_slate(db, draw_code=code, n=14, closes_at=_past())
        _make_official(db, s)
    res = _visible(db, limit_recent=4)
    assert len(res.recent_slates) == 4


def test_weekend_and_midweek_stay_separate(db):
    wk = _seed_slate(db, draw_code="PG-2338", week_type="weekend", n=14, closes_at=_future())
    _make_official(db, wk)
    ms = _seed_slate(db, draw_code="PGM-802", week_type="midweek", n=14, closes_at=_future())
    _make_official(db, ms)
    res = _visible(db)
    types = {s.draw_code: s.week_type for s in res.open_slates}
    assert types == {"PG-2338": "weekend", "PGM-802": "midweek"}


def test_date_suspect_slate_is_held_back_and_diagnosed(db):
    # Official slate whose cierre is in the past relative to creation =>
    # stale_source. Must NOT be open; must appear in discovery.suspect_slates.
    slate = _seed_slate(db, draw_code="PGM-802", week_type="midweek", n=9, closes_at=_past())
    _make_official(db, slate)
    res = _visible(db)
    assert all(s.draw_code != "PGM-802" for s in res.open_slates)
    suspect_codes = [s["draw_code"] for s in res.discovery.suspect_slates]
    assert "PGM-802" in suspect_codes
    entry = next(s for s in res.discovery.suspect_slates if s["draw_code"] == "PGM-802")
    assert entry["date_status"] in {"stale_source", "date_suspect", "needs_operator_confirmation"}


def test_provisional_ms_pdf_window_is_visible_as_open(db):
    slate = _seed_slate(db, draw_code="PGM-804", week_type="midweek", n=9, closes_at=_future())
    _make_official(db, slate)
    db.add(
        ProgolSlateProposalModel(
            draw_code="804",
            week_type="midweek",
            source_name="progol-guia-ln-ms",
            source_url="https://www.loterianacional.gob.mx/Documentos/guiamedia.pdf",
            status="promoted",
            promoted_slate_id=slate.id,
            registration_closes_at=slate.registration_closes_at,
            payload_json=(
                '{"registration_close_source":"provisional_ms_pdf_window",'
                '"extraction_confidence":"provisional",'
                '"fixtures":[{"position":1}],'
                '"block_diagnostics":{"rejected_close_block_draw_code":"800"}}'
            ),
        )
    )
    db.flush()

    res = _visible(db)

    assert res.reason == "open_slate"
    assert [s.draw_code for s in res.open_slates] == ["PGM-804"]
    assert not res.discovery.suspect_slates


def test_provisional_ms_pdf_window_with_result_is_not_open(db):
    slate = _seed_slate(db, draw_code="PGM-804", week_type="midweek", n=9, closes_at=_future())
    _make_official(db, slate)
    db.add(
        ProgolSlateProposalModel(
            draw_code="804",
            week_type="midweek",
            source_name="progol-guia-ln-ms",
            source_url="https://www.loterianacional.gob.mx/Documentos/guiamedia.pdf",
            status="promoted",
            promoted_slate_id=slate.id,
            registration_closes_at=slate.registration_closes_at,
            payload_json=(
                '{"registration_close_source":"provisional_ms_pdf_window",'
                '"extraction_confidence":"provisional",'
                '"fixtures":[{"position":1}],'
                '"block_diagnostics":{"rejected_close_block_draw_code":"800"}}'
            ),
        )
    )
    source = _source(db, "operator-result", priority=60)
    first_match = sorted(slate.matches, key=lambda item: item.position)[0]
    LiveResultService(db).record_observation(
        match_id=first_match.match_id,
        source_id=source.id,
        status=MatchResultStatus.FULL_TIME,
        home_goals=0,
        away_goals=2,
        is_final=True,
        result_code="2",
    )
    db.flush()

    res = _visible(db)

    assert res.reason == "fallback_recent"
    assert res.open_slates == []
    assert [s.draw_code for s in res.recent_slates] == ["PGM-804"]
    assert res.recent_slates[0].read_only is True


def test_date_override_is_traced_and_updates_status(db):
    import asyncio

    from app.api.routes.slates import DateOverrideRequest, date_override

    slate = _seed_slate(db, draw_code="PGM-802", week_type="midweek", n=9, closes_at=_past())
    _make_official(db, slate)
    new_close = _future()
    body = DateOverrideRequest(
        registration_closes_at=new_close,
        reason="acta oficial LN confirmada por operador",
        operator_note="cierre real 30-jun",
    )
    out = asyncio.run(date_override(slate_id=slate.id, body=body, session=db))
    assert out["source_name"] == "operator_date_override"
    assert out["source_type"] == "operator_manual"
    assert out["reason"].startswith("acta oficial")
    assert out["old_registration_closes_at"] is not None
    assert out["new_registration_closes_at"] == new_close.isoformat()
    # Slate updated + un-archived; audit trail persisted as a proposal row.
    from sqlalchemy import select

    from app.models.tables import ProgolSlateProposalModel

    audit = db.scalar(
        select(ProgolSlateProposalModel).where(
            ProgolSlateProposalModel.source_name == "operator_date_override"
        )
    )
    assert audit is not None
    assert audit.status == "operator_override"


def test_discovery_reports_latest_observation(db):
    slate = _seed_slate(db, draw_code="PG-2338", n=14, closes_at=_past())
    _make_official(db, slate)
    db.add(
        ProgolSlateProposalModel(
            draw_code="2339",
            week_type="weekend",
            source_name="LN Progol Guía",
            source_url="https://www.loterianacional.gob.mx/Progol/Guia.pdf",
            status="observed",
        )
    )
    db.flush()
    res = _visible(db)
    assert res.discovery.last_weekend_draw_code == "2339"
    assert res.discovery.last_weekend_status == "observed"
    assert res.discovery.last_observed_at is not None
