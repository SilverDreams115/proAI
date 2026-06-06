"""Fase 2.7 — Tests for the Progol guide PDF parser and the SlateProposal
service's dual-time validation pipeline.

The captured fixture under `tests/fixtures/progol_guia_2335.txt` is a
text excerpt of the GUÍA DE LA QUINIELA CONCURSO 2335 page produced by
`pypdf.extract_text` against the LN PDF. Pinning the parser against the
captured text protects against regex regressions (e.g. when adjusting
the `_FIXTURE_RE` pattern) without needing live network access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.connectors.base import SourceDocument
from app.connectors.progol_guia_pdf import parse_guia_text


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "progol_guia_2335.txt"


def _fixture_text() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_parse_guia_text_extracts_draw_code_and_all_14_fixtures() -> None:
    """End-to-end parse against the captured PG-2335 fixture. Pins the
    14 home/away pairs in their natural order so a regex change that
    drops a fixture (or duplicates one) trips immediately."""
    draw_code, fixtures, closes_at = parse_guia_text(_fixture_text())

    assert draw_code == "2335"
    assert len(fixtures) == 14
    assert [f.position for f in fixtures] == list(range(1, 15))
    # First fixture has the column header "LOCAL VISITANTE" glued to it —
    # the normalizer must strip the prefix before returning the home name.
    assert fixtures[0].home == "MÉXICO"
    assert fixtures[0].away == "AUSTRALIA"
    # Fixture 5 has a stray leading "." carried over from the previous
    # block; lstrip on punctuation should produce "E.U.A." clean.
    assert fixtures[4].home == "E.U.A."
    assert fixtures[4].away == "COLOMBIA"
    # And the last fixture pins the tail so we know the regex didn't
    # truncate early.
    assert fixtures[13].home == "DEGERFORS"
    assert fixtures[13].away == "BROMMAPOJKARNA"


def test_parse_guia_text_parses_cierre_in_utc() -> None:
    """LN publishes "ANTES DE LAS 21:00" Mexico City local time. The DB
    column is UTC-aware, so the parser must convert (UTC-6, no DST in
    2026) → 03:00 UTC the following day."""
    _, _, closes_at = parse_guia_text(_fixture_text())
    assert closes_at is not None
    assert closes_at.tzinfo is not None
    assert closes_at == datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)


def test_parse_guia_text_handles_missing_concurso_gracefully() -> None:
    """A malformed PDF (no CONCURSO header) must return None for the
    draw_code and an empty fixtures list rather than raising."""
    draw_code, fixtures, closes_at = parse_guia_text("NO HEADER\nJUST GIBBERISH\n")
    assert draw_code is None
    assert fixtures == []
    assert closes_at is None


# ---------------------------------------------------------------------------
# Dual-time validation pipeline
# ---------------------------------------------------------------------------


class _StubConnector:
    """Minimal connector stand-in for SlateProposalService.observe().

    Returns the captured PG-2335 text wrapped in a SourceDocument so the
    service exercises real parsing without touching the network.
    """

    name = "progol-guia-ln-weekend"
    kind = "progol_guia_pdf"

    def __init__(self, *, text: str | None = None, draw_code: str = "2335") -> None:
        # Allow callers to override the parsed text to simulate fixture
        # drift between observations.
        self._text = text if text is not None else _fixture_text()
        self._draw_code = draw_code

    def fetch(self) -> list[SourceDocument]:
        from app.connectors.progol_guia_pdf import parse_guia_text

        draw_code, fixtures, closes_at = parse_guia_text(self._text)
        # Allow overriding draw_code without rewriting the whole text.
        draw_code = self._draw_code or draw_code
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
                    "registration_closes_at": closes_at.isoformat() if closes_at else None,
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                    "raw_text_excerpt": self._text[:200],
                },
            )
        ]


def _make_session(tmp_path: Path):
    """Open a fresh file-backed SQLite session with the proposals table
    bootstrapped via migrations. A file path is required because the
    service performs multiple checkouts and `:memory:` would give each
    one its own (empty) database."""
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "proposals.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    # `configure_session` rebinds the module-level `SessionLocal` — use
    # the attribute lookup so we don't grab a stale sessionmaker.
    return db_session.SessionLocal()


def test_proposal_service_first_observation_records_status_observed(tmp_path) -> None:
    """The first sighting of a (draw_code, source_url) pair lands as
    `observed` with observations=1 — never promotes to `validated`
    without a confirming second sighting."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        proposal = service.observe()
        assert proposal is not None
        assert proposal.status == "observed"
        assert proposal.observations == 1
        assert proposal.draw_code == "2335"
        # SQLite returns naive datetimes; semantically this is UTC, so we
        # strip tzinfo from the expected value before comparing.
        closes_at = proposal.registration_closes_at
        if closes_at is not None and closes_at.tzinfo is not None:
            closes_at = closes_at.replace(tzinfo=None)
        assert closes_at == datetime(2026, 5, 31, 3, 0)
    finally:
        session.close()


def test_proposal_service_second_identical_observation_validates(tmp_path) -> None:
    """Two observations with the same fixture signature flip the row to
    `validated`. The signature hashes only draw_code + ordered fixtures,
    so cosmetic changes to `raw_text_excerpt` don't break validation."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        first = service.observe()
        second = service.observe()
        assert first is not None and second is not None
        assert first.id == second.id  # same row, not a duplicate insert
        assert second.status == "validated"
        assert second.observations == 2
    finally:
        session.close()


def test_proposal_service_drift_between_observations_resets_to_observed(tmp_path) -> None:
    """If LN edits the slate between observations the new payload wins
    but the counter resets — we re-require a confirming sighting before
    auto-promote (Fase 3) ever fires."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        original = _fixture_text()
        # Swap fixture #14 home team — same draw_code & source_url so we
        # hit the existing-row branch, but a different signature triggers
        # the drift reset path.
        drifted = original.replace("DEGERFORS", "ALTERNATE FC")

        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        first = service.observe()
        assert first is not None
        assert first.status == "observed"

        drift_service = SlateProposalService(
            session, connector_factory=lambda: _StubConnector(text=drifted)
        )
        drifted_row = drift_service.observe()
        assert drifted_row is not None
        assert drifted_row.id == first.id
        assert drifted_row.status == "observed"  # NOT validated
        assert drifted_row.observations == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# HTTP surface — list / promote
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_proposed_list_endpoint_returns_only_validated_when_filtered(client) -> None:
    """The endpoint must filter by status so the frontend's preview card
    only sees rows that already cleared dual-time validation."""
    from app.db.session import SessionLocal
    from app.services.slate_proposal_service import SlateProposalService

    session = SessionLocal()
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        service.observe()  # observed
        service.observe()  # validated
    finally:
        session.close()

    response = await client.get("/api/slates/proposed?status=validated")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["draw_code"] == "2335"
    assert body[0]["status"] == "validated"
    assert len(body[0]["fixtures"]) == 14


@pytest.mark.anyio
async def test_promote_endpoint_requires_validated_status(client) -> None:
    """Promotion before validation is the operational footgun this
    contract guards against — observed rows must 409 with a clear error."""
    from app.db.session import SessionLocal
    from app.services.slate_proposal_service import SlateProposalService

    session = SessionLocal()
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        proposal = service.observe()  # observed, not validated
        proposal_id = proposal.id
    finally:
        session.close()

    response = await client.post(f"/api/slates/proposed/{proposal_id}/promote")
    assert response.status_code == 409
    assert "validated" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_promote_endpoint_creates_slate_and_flips_proposal(client) -> None:
    """Happy path: validated proposal → 201 with a serialized slate.
    The proposal row also flips to `promoted` so a re-promote attempt
    returns 409."""
    from app.db.session import SessionLocal
    from app.services.slate_proposal_service import SlateProposalService

    session = SessionLocal()
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        service.observe()
        validated = service.observe()
        assert validated.status == "validated"
        proposal_id = validated.id
    finally:
        session.close()

    response = await client.post(f"/api/slates/proposed/{proposal_id}/promote")
    assert response.status_code == 201, response.text
    slate = response.json()
    assert slate["draw_code"] == "PG-2335"
    assert len(slate["matches"]) == 14
    # Kickoffs are spread one hour apart starting cierre + 12h. Position 1
    # therefore lands at cierre + 12h. SQLite drops tzinfo on roundtrip,
    # so we compare wall-clock times in UTC space.
    first_kickoff = datetime.fromisoformat(slate["matches"][0]["kickoff_at"].replace("Z", "+00:00"))
    if first_kickoff.tzinfo is not None:
        first_kickoff = first_kickoff.replace(tzinfo=None)
    assert first_kickoff == datetime(2026, 5, 31, 15, 0)

    # And a second promote attempt should 409.
    second = await client.post(f"/api/slates/proposed/{proposal_id}/promote")
    assert second.status_code == 409
