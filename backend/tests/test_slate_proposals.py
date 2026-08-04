"""Fase 2.7 — Tests for the Progol guide PDF parser and the SlateProposal
service's dual-time validation pipeline.

The captured fixture under `tests/fixtures/progol_guia_2335.txt` is a
text excerpt of the GUÍA DE LA QUINIELA CONCURSO 2335 page produced by
`pypdf.extract_text` against the LN PDF. Pinning the parser against the
captured text protects against regex regressions (e.g. when adjusting
the `_FIXTURE_RE` pattern) without needing live network access.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def test_parse_guia_text_keeps_apostrophe_team_names() -> None:
    """Regression for the PG-2343 pos 9/13 incident. LN prints team names
    with an acute-accent apostrophe ("NEWELL´S", "O´HIGGINS"); the old regex
    excluded it, truncating "NEWELL´S" to "S" and dropping the whole
    "DEPORTES CONCEPCIÓN vs O´HIGGINS" casillero, so the weekend parse
    yielded only 13 fixtures and discovery rejected the concurso."""
    text = (
        "CONCURSO 2343\n"
        "LOCAL VISITANTE\n"
        "NEWELL´S VS\n"
        "CASILLERO 9\n"
        "TALLERES\n"
        "DEPORTES CONCEPCIÓN VS\n"
        "CASILLERO 13\n"
        "O´HIGGINS\n"
    )
    draw_code, fixtures, _ = parse_guia_text(text)
    assert draw_code == "2343"
    by_pos = {f.position: f for f in fixtures}
    assert by_pos[9].home == "NEWELL´S"
    assert by_pos[9].away == "TALLERES"
    assert by_pos[13].home == "DEPORTES CONCEPCIÓN"
    assert by_pos[13].away == "O´HIGGINS"


def test_parse_guia_text_parses_cierre_in_utc() -> None:
    """LN publishes "ANTES DE LAS 21:00" Mexico City local time. The DB
    column is UTC-aware, so the parser must convert (UTC-6, no DST in
    2026) → 03:00 UTC the following day."""
    _, _, closes_at = parse_guia_text(_fixture_text())
    assert closes_at is not None
    assert closes_at.tzinfo is not None
    assert closes_at == datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc)


PG2342_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "progol_guia_2342.txt"


def test_parse_guia_text_keeps_world_cup_placeholders_out_of_revancha() -> None:
    """Regression for the PG-2342 pos 1-2 incident. Casilleros 1-2 carry
    hyphenated World Cup placeholder names ("G FRANCIA-ESPAÑA"); the old
    regex could not parse them and the dedup-by-position guard then let
    the REVANCHA casilleros 1-2 (Juárez-Puebla, Monterrey-S. Laguna) fill
    the regular quiniela's first two positions."""
    draw_code, fixtures, closes_at = parse_guia_text(
        PG2342_FIXTURE_PATH.read_text(encoding="utf-8")
    )

    assert draw_code == "2342"
    assert len(fixtures) == 14
    assert [f.position for f in fixtures] == list(range(1, 15))
    # Positions 1-2 are the World Cup final / third-place placeholders.
    assert fixtures[0].home == "G FRANCIA-ESPAÑA"
    assert fixtures[0].away == "G INGLATERRA-ARGENTINA"
    assert fixtures[1].home == "P FRANCIA-ESPAÑA"
    assert fixtures[1].away == "P INGLATERRA-ARGENTINA"
    # The revancha teams must not leak into the regular slate anywhere.
    all_names = {f.home for f in fixtures} | {f.away for f in fixtures}
    assert "JUÁREZ" not in all_names
    assert "MONTERREY" not in all_names
    # Regular positions 3+ still parse as before.
    assert fixtures[2].home == "PUMAS"
    assert fixtures[2].away == "PACHUCA"
    assert fixtures[13].home == "TÉCNICO UNIVERSITARIO"
    assert fixtures[13].away == "AUCAS"
    # Cierre: viernes 17 de julio 21:00 CDMX → sábado 18 03:00 UTC.
    assert closes_at == datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)


def test_parse_guia_text_stops_at_revancha_even_with_unparsed_positions() -> None:
    """If a regular casillero fails to parse for any reason, the revancha
    section must never backfill its position: casillero numbers restart
    at 1 there, so the first non-increasing position ends parsing."""
    text = PG2342_FIXTURE_PATH.read_text(encoding="utf-8")
    # Cripple casillero 1's VS marker so the position genuinely fails.
    crippled = text.replace("G FRANCIA-ESPAÑA VS", "G FRANCIA-ESPAÑA XX", 1)
    _, fixtures, _ = parse_guia_text(crippled)
    positions = [f.position for f in fixtures]
    assert 1 not in positions
    assert positions == sorted(positions)
    all_names = {f.home for f in fixtures} | {f.away for f in fixtures}
    assert "JUÁREZ" not in all_names


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

    def __init__(
        self,
        *,
        text: str | None = None,
        draw_code: str = "2335",
        week_type: str = "weekend",
        source_url_override: str | None = None,
    ) -> None:
        # Allow callers to override the parsed text to simulate fixture
        # drift between observations.
        self._text = text if text is not None else _fixture_text()
        self._draw_code = draw_code
        self._week_type = week_type
        self._source_url = source_url_override or "https://stub.local/progol/guia.pdf"

    def fetch(self) -> list[SourceDocument]:
        from app.connectors.progol_guia_pdf import parse_guia_text

        draw_code, fixtures, closes_at = parse_guia_text(self._text)
        # Allow overriding draw_code without rewriting the whole text.
        draw_code = self._draw_code or draw_code
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
    """Happy path: validated proposal → 200 with PromoteProposalResponse.
    already_active=False on first promote; the proposal flips to 'promoted'
    so a re-promote attempt returns 409."""
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
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["already_active"] is False
    slate = body["slate"]
    assert slate["draw_code"] == "PG-2335"
    assert len(slate["matches"]) == 14
    # Kickoffs are spread one hour apart starting cierre + 12h. Position 1
    # therefore lands at cierre + 12h. SQLite drops tzinfo on roundtrip,
    # so we compare wall-clock times in UTC space.
    first_kickoff = datetime.fromisoformat(slate["matches"][0]["kickoff_at"].replace("Z", "+00:00"))
    if first_kickoff.tzinfo is not None:
        first_kickoff = first_kickoff.replace(tzinfo=None)
    assert first_kickoff == datetime(2026, 5, 31, 15, 0)

    # And a second promote attempt on the SAME proposal should 409
    # (status == "promoted" guard).
    second = await client.post(f"/api/slates/proposed/{proposal_id}/promote")
    assert second.status_code == 409


@pytest.mark.anyio
async def test_promote_already_active_returns_already_active_flag(client) -> None:
    """If a second (different source_url) proposal for the same draw_code
    is promoted while the first slate is still active and has the same
    composition, the endpoint returns already_active=True and does NOT
    create a duplicate slate row."""
    from app.db.session import SessionLocal
    from app.services.slate_proposal_service import SlateProposalService

    # Seed two validated proposals for the same draw_code but from different
    # source_urls so both can reach validated status independently.
    session = SessionLocal()
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        service.observe()
        validated_a = service.observe()
        assert validated_a.status == "validated"
        first_id = validated_a.id

        # Second proposal: same draw_code, different source_url.
        service_b = SlateProposalService(
            session,
            connector_factory=lambda: _StubConnector(source_url_override="https://stub.local/mirror.pdf"),
        )
        service_b.observe()
        validated_b = service_b.observe()
        assert validated_b.status == "validated"
        second_id = validated_b.id
        assert first_id != second_id
    finally:
        session.close()

    # Promote the first one — fresh slate created.
    r1 = await client.post(f"/api/slates/proposed/{first_id}/promote")
    assert r1.status_code == 200
    assert r1.json()["already_active"] is False
    slate_id_a = r1.json()["slate"]["id"]

    # Promote the second one — same draw_code + same composition_hash.
    r2 = await client.post(f"/api/slates/proposed/{second_id}/promote")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["already_active"] is True
    # Must return the ORIGINAL slate, not a new one.
    assert body2["slate"]["id"] == slate_id_a


@pytest.mark.anyio
async def test_proposed_list_shows_is_already_active(client) -> None:
    """The proposal list endpoint annotates proposals that match an existing
    active slate so the UI can render 'Ya activa / Ver boleta'."""
    from app.db.session import SessionLocal
    from app.services.slate_proposal_service import SlateProposalService

    session = SessionLocal()
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        service.observe()
        validated = service.observe()
        proposal_id = validated.id
    finally:
        session.close()

    # Before promotion: is_already_active must be False.
    before = await client.get("/api/slates/proposed?status=validated")
    assert before.status_code == 200
    proposals_before = before.json()
    assert len(proposals_before) == 1
    assert proposals_before[0]["is_already_active"] is False
    assert proposals_before[0]["active_slate_id"] is None

    # Promote the slate.
    await client.post(f"/api/slates/proposed/{proposal_id}/promote")

    # Seed a second (different source_url) validated proposal for the same draw_code.
    session2 = SessionLocal()
    try:
        service2 = SlateProposalService(
            session2,
            connector_factory=lambda: _StubConnector(source_url_override="https://stub.local/mirror2.pdf"),
        )
        service2.observe()
        service2.observe()
    finally:
        session2.close()

    # After promotion: the second proposal should show is_already_active=True.
    after = await client.get("/api/slates/proposed?status=validated")
    assert after.status_code == 200
    proposals_after = after.json()
    assert len(proposals_after) == 1
    assert proposals_after[0]["is_already_active"] is True
    assert proposals_after[0]["active_slate_id"] is not None


def test_promotion_marks_fabricated_fixtures_and_not_resolved_ones(tmp_path) -> None:
    """A promoted slate must record which of its fixtures it invented.

    The resolver finds nothing for most pairs the LN guide lists, so promotion
    fabricates a fixture at `cierre + 12h` stepped an hour per position. Stored
    unmarked, that construction is indistinguishable from a kickoff a feed
    reported — it reached the pick card as the real hour and a later slate's
    resolver adopted it as a real match. Every fabricated row carries
    `is_placeholder` now; a row backed by an ingested fixture must not.
    """
    from app.models.tables import CompetitionModel, MatchModel, TeamModel
    from app.services.placeholder_fixtures import fallback_kickoff
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session, connector_factory=lambda: _StubConnector())
        service.observe()
        proposal = service.observe()
        assert proposal is not None and proposal.status == "validated"

        cierre = proposal.registration_closes_at
        if cierre.tzinfo is None:
            cierre = cierre.replace(tzinfo=timezone.utc)

        # Give position 1 a real ingested fixture so the resolver matches it.
        fixtures = json.loads(proposal.payload_json)["fixtures"]
        first = fixtures[0]
        competition = CompetitionModel(name="La Liga", country="Spain", season="2026")
        home = TeamModel(name=first["home"])
        away = TeamModel(name=first["away"])
        session.add_all([competition, home, away])
        session.flush()
        real_kickoff = cierre + timedelta(hours=37)
        session.add(
            MatchModel(
                competition=competition,
                home_team=home,
                away_team=away,
                kickoff_at=real_kickoff,
                venue="Santiago Bernabeu",
            )
        )
        session.flush()

        result = service.promote_proposal(proposal)
        slate = result.slate
        by_position = {sm.position: sm.match for sm in slate.matches}

        def _utc(value):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        # Position 1 resolved to the ingested fixture: real kickoff, unmarked.
        assert by_position[1].is_placeholder is False
        assert _utc(by_position[1].kickoff_at) == real_kickoff

        # Every other position was fabricated and says so.
        fabricated = [pos for pos, match in by_position.items() if pos != 1]
        assert fabricated, "expected the guide to carry more than one fixture"
        for position in fabricated:
            assert by_position[position].is_placeholder is True
            assert _utc(by_position[position].kickoff_at) == fallback_kickoff(cierre, position)
    finally:
        session.close()


# --- Operator capture -------------------------------------------------
# Path used when a concurso is live but LN has not published its guía yet:
# the scrapers have nothing to observe, so an operator transcribes the
# programa from an official reseller and the row still carries official
# lineage.


def _capture_kwargs(**overrides):
    payload = {
        "draw_code": "807",
        "week_type": "midweek",
        "source_url": "https://tulotero.mx/resultados/progol-media-semana",
        "fixtures": [
            {"position": 1, "home": "Cincinnati", "away": "Pachuca"},
            {"position": 2, "home": "Columbus Crew", "away": "Atlas"},
        ],
        "closes_at_iso": "2026-08-04T23:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_operator_capture_lands_validated_with_official_lineage(tmp_path) -> None:
    """A TuLotero-sourced capture validates on the first write — a human
    transcribing the programa IS the confirming sighting the two-observation
    rule exists to obtain."""
    from app.services.slate_classification_service import is_official_source_url
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        proposal = SlateProposalService(session).record_operator_capture(**_capture_kwargs())
        assert proposal.status == "validated"
        assert proposal.draw_code == "807"
        assert proposal.week_type == "midweek"
        # The whole point: classify_slate must read this as official lineage.
        assert is_official_source_url(proposal.source_url)
        stored = json.loads(proposal.payload_json)
        assert [f["home"] for f in stored["fixtures"]] == ["Cincinnati", "Columbus Crew"]
        assert stored["capture"]["actor"] == "operator"
    finally:
        session.close()


def test_operator_capture_refuses_unofficial_source(tmp_path) -> None:
    """The allow-list is the only thing standing between a hand-typed
    programa and the dashboard's "official concurso" badge, so a capture
    citing a non-official host is refused rather than written."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session)
        with pytest.raises(ValueError, match="official Progol source URL"):
            service.record_operator_capture(
                **_capture_kwargs(source_url="https://quinielaposible.com/pronostico-807/")
            )
        assert service.list_proposals() == []
    finally:
        session.close()


def test_operator_capture_rejects_gapped_positions(tmp_path) -> None:
    """Positions must be a dense 1..N run. A gap means the operator
    dropped a fixture mid-transcription, which would silently produce a
    short concurso."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session)
        with pytest.raises(ValueError, match="positions must be"):
            service.record_operator_capture(
                **_capture_kwargs(
                    fixtures=[
                        {"position": 1, "home": "Cincinnati", "away": "Pachuca"},
                        {"position": 3, "home": "Columbus Crew", "away": "Atlas"},
                    ]
                )
            )
        assert service.list_proposals() == []
    finally:
        session.close()


def test_operator_capture_is_promotable(tmp_path) -> None:
    """A capture must go through the same promote path as a PDF proposal —
    that is what guarantees fixture resolution and placeholder marking
    behave identically for both lineages."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        service = SlateProposalService(session)
        proposal = service.record_operator_capture(**_capture_kwargs())
        result = service.promote_proposal(proposal)
        assert result.slate.draw_code == "PGM-807"
        assert len(result.slate.matches) == 2
        assert proposal.status == "promoted"
    finally:
        session.close()


def test_operator_capture_records_transcription_note(tmp_path) -> None:
    """`source_url` attests lineage, not that the strings were parsed from
    that page. The note keeps the audit trail from implying a parse that
    never happened."""
    from app.services.slate_proposal_service import SlateProposalService

    session = _make_session(tmp_path)
    try:
        proposal = SlateProposalService(session).record_operator_capture(
            **_capture_kwargs(), note="transcrito de quinielaposible, cruzado vs calendario Leagues Cup"
        )
        stored = json.loads(proposal.payload_json)
        assert stored["capture"]["note"].startswith("transcrito de quinielaposible")
    finally:
        session.close()
