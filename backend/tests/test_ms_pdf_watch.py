"""MS PDF watcher — detects LN corrections and activates only on a valid cierre.

Drives run_ms_pdf_watch with a fake connector so no network is touched:
  * unchanged sha256 → status=unchanged, no reprocess/activation;
  * fixtures 802 + cierre 800 → changed_invalid, slate stays blocked;
  * fixtures 802 + cierre 802 future → changed_valid, slate activated + pre-close
    prediction generated;
  * cierre already past → no activation, no retroactive prediction;
  * no duplicate proposals/slates; Weekend untouched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.connectors.base import SourceDocument
from app.models.tables import ProgolSlateProposalModel
from app.services.ms_pdf_watch_service import run_ms_pdf_watch
from app.services.slate_proposal_service import SlateProposalService

from tests.test_live_results import _make_official, _past, _seed_slate  # noqa: E402


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'mswatch.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _fixtures9():
    teams = [("MEXICO", "ECUADOR"), ("COSTA DE MARFIL", "NORUEGA"), ("BELGICA", "SENEGAL"),
             ("USA", "BOSNIA"), ("ESPANA", "AUSTRIA"), ("PORTUGAL", "CROACIA"),
             ("SUIZA", "ARGELIA"), ("AUSTRALIA", "EGIPTO"), ("ARGENTINA", "CABO VERDE")]
    return [{"position": i + 1, "home": h, "away": a} for i, (h, a) in enumerate(teams)]


class _FakeMsConnector:
    """Returns a controlled MS guide payload (no network)."""

    name = "progol-guia-ln-ms"

    def __init__(self, *, sha, closes_at, accepted, rejected_block=None, draw_code="802", fixtures=None):
        self._sha = sha
        self._closes_at = closes_at
        self._accepted = accepted
        self._rejected = rejected_block
        self._draw = draw_code
        self._fixtures = fixtures if fixtures is not None else _fixtures9()

    def fetch(self):
        payload = {
            "draw_code": self._draw,
            "week_type": "midweek",
            "registration_closes_at": self._closes_at,
            "match_count": len(self._fixtures),
            "fixtures": self._fixtures,
            "pdf_sha256": self._sha,
            "source_url": "https://ln/guiamedia.pdf?v=test",
            "content_length": 1000,
            "block_diagnostics": {
                "fixture_draw_code": self._draw,
                "accepted_close_block": self._accepted,
                "rejected_close_block_draw_code": self._rejected,
                "rejected_close_year": "2025" if self._rejected else None,
            },
        }
        return [SourceDocument(source_name=self.name, source_url=payload["source_url"],
                               captured_at=datetime.now(timezone.utc), payload=payload)]


def _svc(db, connector):
    return SlateProposalService(db, connector_factory=lambda: connector)


def _seed_ms_802(db, *, closes_at):
    slate = _seed_slate(db, draw_code="PGM-802", week_type="midweek", n=9, closes_at=closes_at)
    _make_official(db, slate)
    return slate


def test_stale_invalid_keeps_blocked(db):
    conn = _FakeMsConnector(sha="aaa", closes_at=None, accepted=False, rejected_block="800")
    res = run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    assert res["last_ms_pdf_status"] == "changed_invalid"
    assert res["activated"] is False
    assert "800" in res["reason"]


def test_unchanged_sha_not_reprocessed(db):
    conn = _FakeMsConnector(sha="aaa", closes_at=None, accepted=False, rejected_block="800")
    run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    # Same sha again → unchanged.
    res = run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    assert res["last_ms_pdf_status"] == "unchanged"
    assert res["activated"] is False


def test_corrected_valid_activates_existing_slate(db):
    slate = _seed_ms_802(db, closes_at=_past())  # existing blocked slate (cierre past)
    # Simulate a slate WITHOUT a pre-close snapshot so generation is exercised.
    from app.models.tables import TicketRecommendationSnapshotModel
    db.query(TicketRecommendationSnapshotModel).filter_by(slate_id=slate.id).delete()
    db.flush()
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    conn = _FakeMsConnector(sha="bbb", closes_at=future, accepted=True)
    res = run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=True)
    assert res["last_ms_pdf_status"] == "changed_valid"
    assert res["activated"] is True
    assert res["prediction_generated"] is True
    # Slate now has the PDF cierre and is un-archived.
    from app.repositories.slate_repository import SlateRepository
    slate = next(s for s in SlateRepository(db).list_slates() if s.draw_code == "PGM-802")
    assert slate.registration_closes_at is not None
    assert slate.is_archived is False


def test_valid_but_past_close_does_not_activate_or_predict(db):
    _seed_ms_802(db, closes_at=_past())
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = _FakeMsConnector(sha="ccc", closes_at=past, accepted=True)
    res = run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=True)
    assert res["activated"] is False
    assert res["prediction_generated"] is False
    assert "retroactiv" in res["reason"].lower() or "pasó" in res["reason"].lower()


def test_no_duplicate_proposals(db):
    conn = _FakeMsConnector(sha="aaa", closes_at=None, accepted=False, rejected_block="800")
    run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    n = db.query(ProgolSlateProposalModel).filter_by(draw_code="802", week_type="midweek").count()
    assert n == 1


def test_weekend_untouched(db):
    wk = _seed_slate(db, draw_code="PG-2340", week_type="weekend", n=14, closes_at=_past())
    _make_official(db, wk)
    conn = _FakeMsConnector(sha="aaa", closes_at=None, accepted=False, rejected_block="800")
    run_ms_pdf_watch(db, proposal_service=_svc(db, conn), generate_prediction=False)
    from app.repositories.slate_repository import SlateRepository
    wk2 = next(s for s in SlateRepository(db).list_slates() if s.draw_code == "PG-2340")
    assert wk2.week_type == "weekend"  # unchanged, not merged with MS
