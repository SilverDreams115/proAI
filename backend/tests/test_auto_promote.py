"""Fase 3.2 — Tests for the worker's auto-promote job.

Three behaviors get pinned:

  * Auto-promote fires when the active slate's cierre is within the
    configured threshold.
  * Auto-promote skips when the active slate's cierre is still far off
    — premature promotion would create stale slates days early.
  * Auto-promote fires when no active slate exists at all (transition
    moment: previous concurso just archived, gap before next promote).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.connectors.base import SourceDocument


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "progol_guia_2335.txt"


class _StubConnector:
    name = "progol-guia-ln-weekend"
    kind = "progol_guia_pdf"

    def __init__(self, *, cierre: datetime) -> None:
        self._cierre = cierre

    def fetch(self) -> list[SourceDocument]:
        from app.connectors.progol_guia_pdf import parse_guia_text

        text = FIXTURE_PATH.read_text(encoding="utf-8")
        draw_code, fixtures, _ = parse_guia_text(text)
        return [
            SourceDocument(
                source_name=self.name,
                source_url="https://stub.local/progol/guia.pdf",
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": f"Progol Guía concurso {draw_code}",
                    "summary": f"{len(fixtures)} fixtures parsed.",
                    "draw_code": draw_code,
                    "week_type": "weekend",
                    "registration_closes_at": self._cierre.isoformat(),
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                    "raw_text_excerpt": text[:200],
                },
            )
        ]


def _configure_db(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'auto_promote.db'}")
    run_migrations(db_session.engine)
    return db_session


def _seed_validated_proposal(session, cierre: datetime):
    from app.services.slate_proposal_service import SlateProposalService

    service = SlateProposalService(session, connector_factory=lambda: _StubConnector(cierre=cierre))
    service.observe()
    proposal = service.observe()
    assert proposal.status == "validated"
    return proposal


def _seed_active_slate(session, *, draw_code: str, closes_at: datetime):
    from app.schemas.slate import ProgolSlateCreate
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    payload = ProgolSlateCreate(
        label=f"Active {draw_code}",
        draw_code=draw_code,
        week_type="midweek",
        registration_closes_at=closes_at,
        is_archived=False,
        matches=[
            {
                "position": 1,
                "competition": {"name": "Liga MX", "country": "Mexico", "season": "2026-C"},
                "home_team": {"name": "Local"},
                "away_team": {"name": "Visitante"},
                "kickoff_at": closes_at + timedelta(hours=6),
                "venue": "Stadium",
            }
        ],
    )
    SlateService(SlateRepository(session)).create_slate(payload)


def test_auto_promote_fires_when_active_cierre_within_threshold(tmp_path, monkeypatch) -> None:
    """Active slate closes in 30 minutes (inside 2h threshold) and a
    validated proposal exists → worker promotes it."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 20, 30, tzinfo=timezone.utc)
        _seed_active_slate(session, draw_code="PGM-797", closes_at=now + timedelta(minutes=30))
        proposal = _seed_validated_proposal(session, cierre=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
        proposal_id = proposal.id
        session.commit()
    finally:
        session.close()

    worker = SchedulerWorker()
    new_session = db_session.SessionLocal()
    try:
        worker._maybe_auto_promote_proposals(new_session, datetime(2026, 5, 30, 20, 30, tzinfo=timezone.utc))
    finally:
        new_session.close()

    # Re-open the session to read post-commit state.
    verify_session = db_session.SessionLocal()
    try:
        from app.models.tables import ProgolSlateProposalModel
        refreshed = verify_session.get(ProgolSlateProposalModel, proposal_id)
        assert refreshed.status == "promoted"
        assert refreshed.promoted_slate_id is not None
        assert worker._state.last_auto_promoted_draw_code == "2335"
    finally:
        verify_session.close()


def test_auto_promote_skips_when_active_cierre_far_away(tmp_path, monkeypatch) -> None:
    """Active slate closes in 8 hours (outside the 2h threshold). Even
    though a validated proposal is ready, the worker must NOT promote
    yet — premature promotion would conflict with the still-open
    concurso."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc)
        _seed_active_slate(session, draw_code="PGM-797", closes_at=now + timedelta(hours=8))
        proposal = _seed_validated_proposal(session, cierre=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
        proposal_id = proposal.id
        session.commit()
    finally:
        session.close()

    worker = SchedulerWorker()
    new_session = db_session.SessionLocal()
    try:
        worker._maybe_auto_promote_proposals(new_session, datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc))
    finally:
        new_session.close()

    verify_session = db_session.SessionLocal()
    try:
        from app.models.tables import ProgolSlateProposalModel
        refreshed = verify_session.get(ProgolSlateProposalModel, proposal_id)
        assert refreshed.status == "validated"  # still validated, not promoted
        assert refreshed.promoted_slate_id is None
    finally:
        verify_session.close()


def test_auto_promote_fires_when_no_active_slate(tmp_path, monkeypatch) -> None:
    """Edge case: previous concurso already archived, nothing active.
    With a validated proposal sitting in the queue we want it promoted
    immediately so the dashboard doesn't have a blank period."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        proposal = _seed_validated_proposal(session, cierre=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
        proposal_id = proposal.id
        session.commit()
    finally:
        session.close()

    worker = SchedulerWorker()
    new_session = db_session.SessionLocal()
    try:
        worker._maybe_auto_promote_proposals(new_session, datetime(2026, 5, 30, 21, 0, tzinfo=timezone.utc))
    finally:
        new_session.close()

    verify_session = db_session.SessionLocal()
    try:
        from app.models.tables import ProgolSlateProposalModel
        refreshed = verify_session.get(ProgolSlateProposalModel, proposal_id)
        assert refreshed.status == "promoted"
        assert refreshed.promoted_slate_id is not None
    finally:
        verify_session.close()


def test_auto_promote_disabled_is_a_no_op(tmp_path, monkeypatch) -> None:
    """The feature flag is the operator's kill switch. With it off, the
    worker must leave validated proposals alone regardless of state."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", False)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        proposal = _seed_validated_proposal(session, cierre=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
        proposal_id = proposal.id
        session.commit()
    finally:
        session.close()

    worker = SchedulerWorker()
    new_session = db_session.SessionLocal()
    try:
        worker._maybe_auto_promote_proposals(new_session, datetime(2026, 5, 30, 21, 0, tzinfo=timezone.utc))
    finally:
        new_session.close()

    verify_session = db_session.SessionLocal()
    try:
        from app.models.tables import ProgolSlateProposalModel
        refreshed = verify_session.get(ProgolSlateProposalModel, proposal_id)
        assert refreshed.status == "validated"
        assert refreshed.promoted_slate_id is None
    finally:
        verify_session.close()
