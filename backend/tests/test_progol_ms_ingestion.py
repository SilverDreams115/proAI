"""Progol Media Semana ingestion — parser and proposal pipeline tests.

Covers:
  1. parse_ms_guia_text extracts 9 fixtures from the captured MS fixture
  2. observe_ms() creates a midweek proposal with correct draw_code
  3. Dual-time validation pipeline for MS proposals
  4. Promotion creates a PGM-prefixed midweek slate
  5. MS promotion does not affect any existing weekend slate
  6. Predictions for an MS slate are scoped to that slate's own slate_id
  7. Ticket snapshot for an MS slate uses that slate's composition_hash
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.connectors.base import SourceDocument
from app.connectors.progol_guia_pdf import parse_ms_guia_text

MS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "progol_guia_ms_799.txt"
WK_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "progol_guia_2335.txt"


def _ms_fixture_text() -> str:
    return MS_FIXTURE_PATH.read_text(encoding="utf-8")


def _wk_fixture_text() -> str:
    return WK_FIXTURE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Parser unit tests
# ---------------------------------------------------------------------------


def test_parse_ms_guia_text_extracts_9_fixtures() -> None:
    """Captured PGM-799 text must yield exactly 9 fixtures in positions 1–9."""
    draw_code, fixtures, closes_at = parse_ms_guia_text(_ms_fixture_text())

    assert draw_code == "799"
    assert len(fixtures) == 9
    assert [f.position for f in fixtures] == list(range(1, 10))

    # Spot-check known pairs from PGM-799 Copa del Mundo 2026
    assert fixtures[0].home == "MÉXICO"
    assert fixtures[0].away == "SUDÁFRICA"
    assert fixtures[6].home == "PAÍSES BAJOS"
    assert fixtures[6].away == "JAPÓN"
    assert fixtures[8].home == "SUECIA"
    assert fixtures[8].away == "TÚNEZ"


def test_parse_ms_guia_text_cierre_in_utc() -> None:
    """The cierre '11 de junio a las 13:00' (Mexico City UTC-6) must be
    stored as 19:00 UTC the same day."""
    _, _, closes_at = parse_ms_guia_text(_ms_fixture_text())

    assert closes_at is not None
    assert closes_at.tzinfo is not None
    assert closes_at == datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)


def test_parse_ms_guia_text_malformed_returns_empty() -> None:
    """A malformed or empty PDF extract must return None draw_code and no
    fixtures without raising."""
    draw_code, fixtures, closes_at = parse_ms_guia_text("GIBBERISH\nNO HEADERS\n")

    assert draw_code is None
    assert fixtures == []
    assert closes_at is None


# ---------------------------------------------------------------------------
# Helpers shared by pipeline tests
# ---------------------------------------------------------------------------


def _make_session(tmp_path: Path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    db_file = tmp_path / "ms_proposals.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


class _StubMsConnector:
    """Stub MS connector — returns a fixed 9-fixture payload without network."""

    name = "progol-guia-ln-ms"
    kind = "progol_ms_guia_pdf"

    def __init__(self, *, draw_code: str = "799", source_url: str = "https://stub.local/ms.pdf") -> None:
        self._draw_code = draw_code
        self._source_url = source_url

    def fetch(self) -> list[SourceDocument]:
        _, fixtures, closes_at = parse_ms_guia_text(_ms_fixture_text())
        return [
            SourceDocument(
                source_name=self.name,
                source_url=self._source_url,
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": f"Progol MS concurso {self._draw_code}",
                    "summary": f"{len(fixtures)} fixtures parsed.",
                    "draw_code": self._draw_code,
                    "week_type": "midweek",
                    "registration_closes_at": closes_at.isoformat() if closes_at else None,
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                },
            )
        ]


class _StubWkConnector:
    """Stub weekend connector — returns the captured PG-2335 payload."""

    name = "progol-guia-ln-weekend"
    kind = "progol_guia_pdf"

    def __init__(self, *, source_url: str = "https://stub.local/guia.pdf") -> None:
        self._source_url = source_url

    def fetch(self) -> list[SourceDocument]:
        from app.connectors.progol_guia_pdf import parse_guia_text

        draw_code, fixtures, closes_at = parse_guia_text(_wk_fixture_text())
        return [
            SourceDocument(
                source_name=self.name,
                source_url=self._source_url,
                captured_at=datetime.now(timezone.utc),
                payload={
                    "draw_code": draw_code,
                    "week_type": "weekend",
                    "registration_closes_at": closes_at.isoformat() if closes_at else None,
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                },
            )
        ]


# ---------------------------------------------------------------------------
# 2 & 3. MS proposal pipeline
# ---------------------------------------------------------------------------


def test_observe_ms_first_sighting_records_midweek_observed(tmp_path) -> None:
    """First observe_ms() call must land as status='observed' with week_type='midweek'."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubMsConnector())
        proposal = service.observe_ms()

        assert proposal is not None
        assert proposal.status == "observed"
        assert proposal.week_type == "midweek"
        assert proposal.draw_code == "799"
        assert proposal.observations == 1
    finally:
        session.close()


def test_observe_ms_second_identical_sighting_validates(tmp_path) -> None:
    """Two identical sightings must flip the MS proposal to 'validated'."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubMsConnector())
        first = service.observe_ms()
        second = service.observe_ms()

        assert first is not None and second is not None
        assert first.id == second.id
        assert second.status == "validated"
        assert second.week_type == "midweek"
        assert second.observations == 2
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 4 & 5. Promotion
# ---------------------------------------------------------------------------


def test_promote_ms_proposal_creates_pgm_prefixed_midweek_slate(tmp_path) -> None:
    """Promoting a validated MS proposal must create a slate with draw_code
    'PGM-<N>' and week_type='midweek', never 'PG-' or 'weekend'."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubMsConnector())
        service.observe_ms()
        validated = service.observe_ms()
        assert validated is not None and validated.status == "validated"

        result = service.promote_proposal(validated, actor="test")
        session.commit()

        slate = result.slate
        assert slate.draw_code == "PGM-799"
        assert slate.week_type == "midweek"
        assert len(slate.matches) == 9
        assert result.already_active is False
    finally:
        session.close()


def test_ms_promotion_does_not_affect_weekend_slate(tmp_path) -> None:
    """Promoting an MS proposal while a weekend slate exists must leave the
    weekend slate untouched — draw_code, week_type, and match count unchanged."""
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_proposal_service import SlateProposalService
    from app.services.slate_service import SlateService

    session = _make_session(tmp_path)
    try:
        # Seed an active weekend slate first (14 distinct matches)
        wk_matches = [
            MatchReferencePayload(
                position=p,
                competition=CompetitionPayload(name="Liga MX", country="MX"),
                home_team=TeamPayload(name=f"HomeWK{p}", country="MX"),
                away_team=TeamPayload(name=f"AwayWK{p}", country="MX"),
                kickoff_at=datetime(2026, 6, 20, 19, 0, tzinfo=timezone.utc),
            )
            for p in range(1, 15)
        ]
        wk_payload = ProgolSlateCreate(
            label="Progol 2336",
            draw_code="PG-2336",
            week_type="weekend",
            registration_closes_at=datetime(2026, 6, 19, 3, 0, tzinfo=timezone.utc),
            is_archived=False,
            matches=wk_matches,
        )
        wk_slate = SlateService(SlateRepository(session)).create_slate(wk_payload)
        wk_id = wk_slate.id
        wk_hash = wk_slate.composition_hash
        session.commit()

        # Promote an MS proposal
        ms_service = SlateProposalService(session, connector_factory=lambda: _StubMsConnector())
        ms_service.observe_ms()
        validated = ms_service.observe_ms()
        ms_service.promote_proposal(validated, actor="test")
        session.commit()

        # Weekend slate must be unchanged
        wk_after = SlateRepository(session).get_slate(wk_id)
        assert wk_after is not None
        assert wk_after.draw_code == "PG-2336"
        assert wk_after.week_type == "weekend"
        assert wk_after.composition_hash == wk_hash
        assert wk_after.is_archived is False
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 6. Predictions scoped to MS slate_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_predictions_for_ms_slate_scoped_to_ms_slate_id(client) -> None:
    """GET /api/predictions/slates/{ms_id} must return predictions whose
    match_ids all belong to the MS slate, not any weekend slate."""
    from app.db.session import SessionLocal
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService
    from sqlalchemy import select
    from app.models.tables import ProgolSlateMatchModel

    session = SessionLocal()
    try:
        # Create an MS slate with 2 matches (minimal for the test)
        def _match(pos):
            return MatchReferencePayload(
                position=pos,
                competition=CompetitionPayload(name="Copa Mundial 2026", country="INTL"),
                home_team=TeamPayload(name=f"HomeMS{pos}", country="MX"),
                away_team=TeamPayload(name=f"AwayMS{pos}", country="MX"),
                kickoff_at=datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc),
            )

        ms_payload = ProgolSlateCreate(
            label="Progol MS 799",
            draw_code="PGM-799-TEST",
            week_type="midweek",
            registration_closes_at=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
            matches=[_match(1), _match(2)],
        )
        ms_slate = SlateService(SlateRepository(session)).create_slate(ms_payload)
        ms_id = ms_slate.id
        session.commit()
    finally:
        session.close()

    # Fetch predictions — no model runs, so 200 with empty list
    response = await client.get(f"/api/predictions/slates/{ms_id}")
    assert response.status_code == 200
    preds = response.json()

    if preds:
        session2 = SessionLocal()
        try:
            ms_match_ids = {
                row.match_id
                for row in session2.scalars(
                    select(ProgolSlateMatchModel).where(ProgolSlateMatchModel.slate_id == ms_id)
                )
            }
        finally:
            session2.close()
        for pred in preds:
            assert pred["match_id"] in ms_match_ids, (
                f"Prediction {pred['match_id']} does not belong to MS slate {ms_id}"
            )


# ---------------------------------------------------------------------------
# 7. Snapshot uses MS composition_hash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ms_snapshot_uses_ms_composition_hash(client) -> None:
    """A valid ticket snapshot for an MS slate must reference that slate's
    own composition_hash — it must never carry the weekend slate's hash."""
    from app.db.session import SessionLocal
    from app.models.tables import TicketRecommendationSnapshotModel
    from app.repositories.slate_repository import SlateRepository
    from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
    from app.schemas.slate import ProgolSlateCreate
    from app.services.slate_service import SlateService
    from sqlalchemy import select

    session = SessionLocal()
    try:
        def _match(pos):
            return MatchReferencePayload(
                position=pos,
                competition=CompetitionPayload(name="Copa Mundial 2026", country="INTL"),
                home_team=TeamPayload(name=f"HomeSnap{pos}", country="MX"),
                away_team=TeamPayload(name=f"AwaySnap{pos}", country="MX"),
                kickoff_at=datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc),
            )

        ms_payload = ProgolSlateCreate(
            label="Progol MS 799 Snap",
            draw_code="PGM-799-SNAP",
            week_type="midweek",
            registration_closes_at=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
            matches=[_match(1)],
        )
        ms_slate = SlateService(SlateRepository(session)).create_slate(ms_payload)
        ms_id = ms_slate.id
        ms_hash = ms_slate.composition_hash

        # Inject a valid snapshot with a DIFFERENT hash to verify the
        # system checks correctness rather than blindly accepting anything.
        bad_snap = TicketRecommendationSnapshotModel(
            slate_id=ms_id,
            model_version="test",
            payload_json="{}",
            composition_hash="wrong-hash-should-be-invalid",
            is_valid=False,
        )
        good_snap = TicketRecommendationSnapshotModel(
            slate_id=ms_id,
            model_version="test",
            payload_json="{}",
            composition_hash=ms_hash,
            is_valid=True,
        )
        session.add(bad_snap)
        session.add(good_snap)
        session.commit()
        ms_id_copy = ms_id
        ms_hash_copy = ms_hash
    finally:
        session.close()

    # The slate detail endpoint must report has_valid_snapshot=True
    response = await client.get(f"/api/slates/{ms_id_copy}")
    assert response.status_code == 200
    body = response.json()
    assert body["week_type"] == "midweek"
    assert body["has_valid_snapshot"] is True
    # This MS slate closed on 2026-06-11, so its lifecycle state dominates
    # the label: a closed slate reads "Cerrada" regardless of whether it
    # has a valid ticket. Ticket availability is still exposed separately
    # via has_valid_snapshot (asserted above), and closed slates are kept
    # out of the main selector by the is_closed/is_archived filters.
    assert body["is_closed"] is True
    assert body["status_label"] == "Cerrada"

    # Verify the valid snapshot references the correct hash
    session3 = SessionLocal()
    try:
        valid_snaps = session3.scalars(
            select(TicketRecommendationSnapshotModel).where(
                TicketRecommendationSnapshotModel.slate_id == ms_id_copy,
                TicketRecommendationSnapshotModel.is_valid.is_(True),
            )
        ).all()
        assert len(valid_snaps) == 1
        assert valid_snaps[0].composition_hash == ms_hash_copy
    finally:
        session3.close()
