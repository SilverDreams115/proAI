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

    def __init__(
        self,
        *,
        cierre: datetime,
        draw_code_override: str | None = None,
        week_type: str = "weekend",
        source_url: str = "https://stub.local/progol/guia.pdf",
    ) -> None:
        self._cierre = cierre
        self._draw_code_override = draw_code_override
        self._week_type = week_type
        self._source_url = source_url

    def fetch(self) -> list[SourceDocument]:
        from app.connectors.progol_guia_pdf import parse_guia_text

        text = FIXTURE_PATH.read_text(encoding="utf-8")
        draw_code, fixtures, _ = parse_guia_text(text)
        if self._draw_code_override:
            draw_code = self._draw_code_override
        return [
            SourceDocument(
                source_name=self.name,
                source_url=self._source_url,
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": f"Progol Guía concurso {draw_code}",
                    "summary": f"{len(fixtures)} fixtures parsed.",
                    "draw_code": draw_code,
                    "week_type": self._week_type,
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


def _seed_validated_proposal(
    session,
    cierre: datetime,
    *,
    draw_code_override: str | None = None,
    week_type: str = "weekend",
    source_url: str = "https://stub.local/progol/guia.pdf",
):
    from app.services.slate_proposal_service import SlateProposalService

    def _factory():
        return _StubConnector(
            cierre=cierre,
            draw_code_override=draw_code_override,
            week_type=week_type,
            source_url=source_url,
        )

    service = SlateProposalService(session, connector_factory=_factory)
    service.observe()
    proposal = service.observe()
    assert proposal.status == "validated"
    return proposal


def _seed_active_slate(session, *, draw_code: str, closes_at: datetime, week_type: str = "midweek"):
    from app.schemas.slate import ProgolSlateCreate
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    payload = ProgolSlateCreate(
        label=f"Active {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
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
    """Active WEEKEND slate closes in 30 minutes (inside 2h threshold) and
    a validated weekend proposal exists → worker promotes it."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 20, 30, tzinfo=timezone.utc)
        # Active WEEKEND slate closing in 30 minutes (same week_type as proposal).
        _seed_active_slate(session, draw_code="PG-2334", closes_at=now + timedelta(minutes=30), week_type="weekend")
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
    """Active WEEKEND slate closes in 8 hours (outside the 2h threshold).
    Even though a validated weekend proposal is ready, the worker must NOT
    promote yet — premature promotion would conflict with the still-open
    concurso. The check is week_type-scoped so a midweek active slate does
    not block a weekend proposal."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc)
        # Active WEEKEND slate with cierre 8h away — same week_type as proposal.
        _seed_active_slate(session, draw_code="PG-2334", closes_at=now + timedelta(hours=8), week_type="weekend")
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


def test_midweek_active_slate_does_not_block_weekend_proposal(tmp_path, monkeypatch) -> None:
    """A midweek slate closing in 8h must NOT block a weekend proposal from
    being promoted.  Week_type isolation means each type has its own gate."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc)
        # Midweek (MS) active slate far from cierre — should NOT block weekend.
        _seed_active_slate(session, draw_code="PGM-797", closes_at=now + timedelta(hours=8), week_type="midweek")
        # Weekend proposal — no active weekend slate, so it qualifies immediately.
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
        assert refreshed.status == "promoted", "midweek slate must not block weekend promotion"
        assert refreshed.promoted_slate_id is not None
    finally:
        verify_session.close()


def test_auto_promote_concurrent_weekend_and_ms(tmp_path, monkeypatch) -> None:
    """Both a weekend and a midweek/MS proposal can be promoted in the same
    cycle when both their corresponding active slates are within threshold
    (or absent). They must create independent slates, each with its own
    draw_code, week_type, and composition_hash."""
    from app.core.settings import settings
    from app.workers.scheduler_worker import SchedulerWorker
    from app.models.tables import ProgolSlateProposalModel, ProgolSlateModel

    monkeypatch.setattr(settings, "progol_auto_promote_enabled", True)
    monkeypatch.setattr(settings, "progol_auto_promote_threshold_hours", 2.0)

    db_session = _configure_db(tmp_path)
    session = db_session.SessionLocal()
    try:
        now = datetime(2026, 5, 30, 20, 30, tzinfo=timezone.utc)
        cierre_weekend = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
        cierre_ms = datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc)

        # Active weekend slate closing in 30 min.
        _seed_active_slate(session, draw_code="PG-2334", closes_at=now + timedelta(minutes=30), week_type="weekend")
        # Active MS slate closing in 30 min.
        _seed_active_slate(session, draw_code="PGM-796", closes_at=now + timedelta(minutes=30), week_type="midweek")

        # Validated weekend proposal.
        p_wk = _seed_validated_proposal(session, cierre=cierre_weekend, week_type="weekend")
        # Validated MS proposal — different draw_code so they don't collide.
        p_ms = _seed_validated_proposal(
            session,
            cierre=cierre_ms,
            draw_code_override="797",
            week_type="midweek",
            source_url="https://stub.local/ms.pdf",
        )
        wk_id = p_wk.id
        ms_id = p_ms.id
        session.commit()
    finally:
        session.close()

    worker = SchedulerWorker()
    new_session = db_session.SessionLocal()
    try:
        worker._maybe_auto_promote_proposals(new_session, now)
    finally:
        new_session.close()

    verify_session = db_session.SessionLocal()
    try:
        wk_row = verify_session.get(ProgolSlateProposalModel, wk_id)
        ms_row = verify_session.get(ProgolSlateProposalModel, ms_id)
        assert wk_row.status == "promoted", "weekend proposal must be promoted"
        assert ms_row.status == "promoted", "MS proposal must be promoted"
        assert wk_row.promoted_slate_id != ms_row.promoted_slate_id, "must create separate slates"

        # Confirm each slate has the correct week_type.
        wk_slate = verify_session.get(ProgolSlateModel, wk_row.promoted_slate_id)
        ms_slate = verify_session.get(ProgolSlateModel, ms_row.promoted_slate_id)
        assert wk_slate.week_type == "weekend"
        assert ms_slate.week_type == "midweek"

        # Composition hashes must differ (different fixtures/teams/kickoffs).
        assert wk_slate.composition_hash != ms_slate.composition_hash
    finally:
        verify_session.close()


def test_promote_does_not_create_duplicate_slate_same_hash(tmp_path) -> None:
    """Service-layer: promoting a second proposal with the same draw_code
    and same composition_hash returns already_active=True and never creates
    a second progol_slates row."""
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session
    from app.models.tables import ProgolSlateModel
    from app.services.slate_proposal_service import SlateProposalService
    from sqlalchemy import select, func

    configure_session(f"sqlite:///{tmp_path / 'dedup.db'}")
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        cierre = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)

        # First proposal: observe + validate + promote.
        svc_a = SlateProposalService(
            session,
            connector_factory=lambda: _StubConnector(cierre=cierre, source_url="https://a.test/guia.pdf"),
        )
        svc_a.observe()
        prop_a = svc_a.observe()
        result_a = svc_a.promote_proposal(prop_a, actor="test")
        session.commit()
        assert result_a.already_active is False

        # Second proposal: same draw_code but different source_url → both validated.
        svc_b = SlateProposalService(
            session,
            connector_factory=lambda: _StubConnector(cierre=cierre, source_url="https://b.test/guia.pdf"),
        )
        svc_b.observe()
        prop_b = svc_b.observe()
        result_b = svc_b.promote_proposal(prop_b, actor="test")
        session.commit()
        assert result_b.already_active is True
        assert result_b.slate.id == result_a.slate.id

        # Only ONE slate row must exist for draw_code "PG-2335".
        count = session.scalar(
            select(func.count()).select_from(ProgolSlateModel).where(
                ProgolSlateModel.draw_code == "PG-2335"
            )
        )
        assert count == 1, f"Expected 1 slate, found {count}"
    finally:
        session.close()
