"""Fetches the Lotería Nacional Progol guide PDF and extracts the upcoming
contest's 14 fixtures (Fase 2).

The PDF is the canonical source: every weekend LN publishes a single-page
"GUÍA DE LA QUINIELA CONCURSO XXXX" with the 14 local-vs-visitante pairs
plus the venta dates. The connector:

  1. Fetches the LN /Progol/Quiniela landing page.
  2. Scrapes the latest PDF URL from the page (the URL has a version
     query string that changes when LN republishes; resolving it on
     every fetch keeps us aligned with whatever they last published).
  3. Downloads the PDF and runs pypdf text extraction.
  4. Pulls CONCURSO number, 14 fixtures and the venta cierre datetime
     via regex over the extracted text.

The output `SourceDocument.payload` is purposefully thin — just the
fields the SlateProposalService consumes. Heavy lifting like entity
resolution lives outside this connector so the parser stays unit-
testable with a captured PDF fixture.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from urllib.request import Request

from pypdf import PdfReader

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


_GUIA_PDF_HREF_RE = re.compile(
    r'href="([^"]*progol_guia_quiniela/GUIA\.pdf[^"]*)"',
    flags=re.IGNORECASE,
)
_FALLBACK_GUIA_URL = (
    "https://www.loterianacional.gob.mx/Documentos/juegos/Concursosysorteos/"
    "progol_guia_quiniela/GUIA.pdf"
)
_CONCURSO_RE = re.compile(r"CONCURSO\s+(\d{3,5})", flags=re.IGNORECASE)
# Fixture pattern: HOME VS\nCASILLERO N\nAWAY — allowing whitespace and
# accented characters in the team names. Greedy capture limited by the
# explicit VS / CASILLERO markers.
_FIXTURE_RE = re.compile(
    r"([A-ZÁÉÍÓÚÑÜ.][A-ZÁÉÍÓÚÑÜ.\s]*?)\s+VS\s*\n\s*CASILLERO\s+(\d{1,2})\s*\n\s*([A-ZÁÉÍÓÚÑÜ.][A-ZÁÉÍÓÚÑÜ.\s]*?)(?:\n|$)",
    flags=re.MULTILINE,
)
_VENTA_RE = re.compile(
    r"VENTA\s+DEL\s+(?P<from_day>\w+)\s+(?P<from_date>\d+)\s+AL\s+(?P<to_day>\w+)\s+"
    r"(?P<to_date>\d+)\s+DE\s+(?P<month>\w+)\s+DE\s+(?P<year>\d{4})\s+ANTES\s+DE\s+LAS\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})",
    flags=re.IGNORECASE,
)

_SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


@dataclass(frozen=True)
class _Fixture:
    position: int
    home: str
    away: str


class ProgolGuiaPdfConnector(SourceConnector):
    """One source = one Progol guide page (weekend or media-semana).

    The `landing_url` defaults to LN's weekend Quiniela page. Operators
    can override it via the `SourceModel.base_url` to point at the
    media-semana page when that's what they want to scrape.
    """

    DEFAULT_LANDING_URL = "https://www.loterianacional.gob.mx/Progol/Quiniela"

    def __init__(
        self,
        name: str,
        base_url: str | None = None,
        week_type: str = "weekend",
    ) -> None:
        self.name = name
        self.kind = "progol_guia_pdf"
        self.base_url = base_url or self.DEFAULT_LANDING_URL
        self.week_type = week_type
        self.description = "LN Progol PDF guide — official upcoming contest source."

    def fetch(self) -> list[SourceDocument]:
        captured = datetime.now(timezone.utc)
        pdf_url = self._resolve_pdf_url()
        pdf_bytes = self._download_bytes(pdf_url)
        text = self._extract_text(pdf_bytes)
        draw_code, fixtures, closes_at = parse_guia_text(text)
        return [
            SourceDocument(
                source_name=self.name,
                source_url=pdf_url,
                captured_at=captured,
                payload={
                    "title": f"Progol Guía concurso {draw_code}" if draw_code else "Progol Guía",
                    "summary": (
                        f"Concurso {draw_code}, {len(fixtures)} fixtures parsed."
                    ),
                    "draw_code": draw_code,
                    "week_type": self.week_type,
                    "registration_closes_at": closes_at.isoformat() if closes_at else None,
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                    "raw_text_excerpt": text[:600],
                },
            )
        ]

    def _resolve_pdf_url(self) -> str:
        # Fetch the landing HTML and scrape the versioned PDF href. The
        # query string (`?v=YYYYMMDDhhmmss`) changes weekly when LN
        # republishes; resolving on every fetch keeps us aligned with the
        # most recent guide rather than caching a stale cdn url.
        try:
            request = Request(
                self.base_url,
                headers={
                    "User-Agent": "proAI/0.1 (+https://local.proai)",
                    "Accept": "text/html",
                },
            )
            with urlopen(request, timeout=15) as response:
                html = response.read().decode("utf-8", errors="replace")
        except Exception:
            return _FALLBACK_GUIA_URL
        match = _GUIA_PDF_HREF_RE.search(html)
        if match is None:
            return _FALLBACK_GUIA_URL
        return urljoin(self.base_url, match.group(1))

    def _download_bytes(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "User-Agent": "proAI/0.1 (+https://local.proai)",
                "Accept": "application/pdf",
            },
        )
        with urlopen(request, timeout=30) as response:
            return response.read()

    def _extract_text(self, pdf_bytes: bytes) -> str:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(chunks)


def parse_guia_text(text: str) -> tuple[str | None, list[_Fixture], datetime | None]:
    """Extract (draw_code, regular_fixtures, registration_closes_at).

    Public so tests can exercise the parser against captured PDF text
    without round-tripping bytes. Revancha fixtures are out of scope:
    Progol pays on the regular slate and the auto-transition only needs
    those 14 partidos. Subsequent fases can extend the return tuple.
    """
    draw_match = _CONCURSO_RE.search(text)
    draw_code = draw_match.group(1) if draw_match else None

    fixtures: list[_Fixture] = []
    seen_positions: set[int] = set()
    for fixture_match in _FIXTURE_RE.finditer(text):
        position = int(fixture_match.group(2))
        # The first 14 distinct CASILLERO numbers correspond to the
        # regular concurso. CASILLERO 1-7 will repeat for revancha; the
        # set membership guard keeps us on the first pass only.
        if position in seen_positions:
            break
        seen_positions.add(position)
        home = _normalize_team(fixture_match.group(1))
        away = _normalize_team(fixture_match.group(3))
        if not home or not away:
            continue
        fixtures.append(_Fixture(position=position, home=home, away=away))
        if len(fixtures) == 14:
            break

    closes_at = _parse_cierre(text)
    return draw_code, fixtures, closes_at


_TEAM_PREFIX_NOISE = (
    "LOCAL VISITANTE",
    "GUÍA DE LA QUINIELA",
    "LOCAL",
    "VISITANTE",
)


def _normalize_team(raw: str) -> str:
    # The first fixture inherits the column header "LOCAL VISITANTE" and
    # several team names are preceded by stray punctuation from the
    # description block. We strip both before returning so downstream
    # entity resolution gets a clean string to match against aliases.
    cleaned = " ".join(raw.split())
    cleaned = cleaned.lstrip(".,;:- ").strip()
    for prefix in _TEAM_PREFIX_NOISE:
        if cleaned.upper().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned


def _parse_cierre(text: str) -> datetime | None:
    match = _VENTA_RE.search(text)
    if match is None:
        return None
    try:
        day = int(match.group("to_date"))
        month = _SPANISH_MONTHS.get(match.group("month").lower())
        year = int(match.group("year"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
    except (TypeError, ValueError):
        return None
    if month is None:
        return None
    # LN venta cuts at the listed hour local Mexico City time (UTC-6,
    # no DST in 2026). The DB stores UTC so the comparison against
    # NOW() in the auto-archive worker stays portable.
    mx_tz = timezone(timedelta(hours=-6))
    local_dt = datetime(year, month, day, hour, minute, tzinfo=mx_tz)
    return local_dt.astimezone(timezone.utc)
