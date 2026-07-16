"""Fetches LN Progol guide PDFs (weekend and Media Semana) and extracts upcoming
contest fixtures.

Two connectors:
- ProgolGuiaPdfConnector: LN /Progol/Quiniela page → 14-fixture weekend PDF
- ProgolMsGuiaPdfConnector: LN /ProgolMediaSemana/Quiniela page → 9-fixture MS PDF

Both PDFs share the CONCURSO / CASILLERO / LOCAL / VISITANTE format, but
the MS PDF has:
  - 9 fixtures instead of 14
  - CIERRE DE VENTA (not VENTA DEL...) with a different date layout
  - Some fixtures with home team names not preceded by VS (PDF 2-column artifact)
  - Some home teams appearing at the document tail (column-order artifact)
"""
from __future__ import annotations

import hashlib
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
# explicit VS / CASILLERO markers. Hyphens are part of the alphabet because
# tournament placeholder fixtures print composite names ("G FRANCIA-ESPAÑA
# VS G INGLATERRA-ARGENTINA" for the 2026 World Cup final): without them the
# casillero silently fails to parse and its position can be filled by the
# wrong section (the PG-2342 pos 1-2 incident).
_FIXTURE_RE = re.compile(
    r"([A-ZÁÉÍÓÚÑÜ.][A-ZÁÉÍÓÚÑÜ.\s-]*?)\s+VS\s*\n\s*CASILLERO\s+(\d{1,2})\s*\n\s*([A-ZÁÉÍÓÚÑÜ.][A-ZÁÉÍÓÚÑÜ.\s-]*?)(?:\n|$)",
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
    last_position = 0
    for fixture_match in _FIXTURE_RE.finditer(text):
        position = int(fixture_match.group(2))
        # Casillero numbers are strictly increasing within the regular
        # concurso; the revancha section restarts at CASILLERO 1. Stopping
        # on the first non-increase keeps revancha fixtures out even when
        # an earlier regular casillero failed to parse — a dedup-by-number
        # guard instead lets revancha 1-2 masquerade as regular positions
        # 1-2 (the PG-2342 incident).
        if position <= last_position:
            break
        last_position = position
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


# ---------------------------------------------------------------------------
# Progol Media Semana PDF support
# ---------------------------------------------------------------------------

_MS_GUIA_PDF_HREF_RE = re.compile(
    r'href="([^"]*progol_media_guia_quiniela/guiamedia\.pdf[^"]*)"',
    flags=re.IGNORECASE,
)
_MS_FALLBACK_URL = (
    "https://www.loterianacional.gob.mx/Documentos/juegos/Concursosysorteos/"
    "progol_media_guia_quiniela/guiamedia.pdf"
)

# MS cierre: "CIERRE DE VENTA\nConcurso NNN\nJueves 11 de junio hasta las \n13:00 horas"
# We capture the block's OWN concurso number so we can reject a cierre that
# belongs to a different (older) concurso than the guide's fixtures — the
# exact PGM-802 staleness bug, where the fixtures are 802 but the cierre block
# still reads "Concurso 800 ... 16 de junio ... de 2025". The trailing year on
# the nearby "Juegos del ... de YYYY" line is captured when present (the cierre
# line itself carries no year) so we never blindly assume the current year.
_MS_VENTA_RE = re.compile(
    r"(?:CIERRE\s+DE\s+VENTA|VENTA)\s*\n"
    r"Concurso\s+(?P<concurso>\d+)\s*\n"
    r"\w+\s+(?P<day>\d{1,2})\s+de\s+(?P<month>\w+)\s+hasta\s+las\s+\n?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?:[^\n]*\n[^\n]*?\bde\s+(?P<year>\d{4}))?",
    re.IGNORECASE,
)

# Pattern to match home team followed by VS (with optional whitespace/newlines between)
_MS_HOME_VS_RE = re.compile(
    r"([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ.\s]{0,60}?)\s*\n?\s*VS\s*(?:\n|(?=\s*CASILLERO))",
    re.MULTILINE,
)


def _ms_clean(raw: str) -> str:
    return " ".join(raw.split()).strip(".,;: ")


def parse_ms_guia_text(text: str) -> tuple[str | None, list[_Fixture], datetime | None]:
    """Parse the LN Progol Media Semana PDF text.

    The MS PDF uses a 2-column layout that pypdf serialises non-linearly,
    causing some home team names to appear AFTER all CASILLERO markers.
    We handle this with a 3-step strategy:
      1. Extract away teams (text immediately after each CASILLERO N)
      2. Match home teams from VS markers that precede each CASILLERO
      3. Rescue orphan home teams at end-of-text (2-column artifact)
    """
    draw_match = _CONCURSO_RE.search(text)
    draw_code = draw_match.group(1) if draw_match else None

    # Step 1: extract all casillero positions and away teams
    casillero_spans: list[tuple[int, int, int]] = []  # (start, pos, end)
    for m in re.finditer(r"CASILLERO\s+(\d{1,2})", text):
        casillero_spans.append((m.start(), int(m.group(1)), m.end()))

    away_map: dict[int, str] = {}
    for _, pos, end_idx in casillero_spans:
        after = text[end_idx:]
        for line in after.split("\n"):
            stripped = line.strip()
            if stripped and re.match(r"[A-ZÁÉÍÓÚÜÑ]", stripped):
                away_map[pos] = _normalize_team(stripped)
                break

    # Step 2: match home teams from VS markers closest BEFORE each CASILLERO
    vs_occurrences: list[tuple[int, str]] = []  # (end_of_vs, home_raw)
    for m in _MS_HOME_VS_RE.finditer(text):
        home_raw = _ms_clean(m.group(1))
        if home_raw and home_raw not in {"LOCAL", "VISITANTE", "LOCAL VISITANTE"}:
            vs_occurrences.append((m.end(), home_raw))

    home_map: dict[int, str] = {}
    for i, (cas_start, pos, _) in enumerate(casillero_spans):
        # Only consider VS markers that fall in the gap between the PREVIOUS
        # CASILLERO and THIS CASILLERO so the same VS is never shared.
        prev_end = casillero_spans[i - 1][2] if i > 0 else 0
        candidates = [
            (vs_end, home)
            for vs_end, home in vs_occurrences
            if prev_end <= vs_end <= cas_start + 5
        ]
        if candidates:
            vs_end, home_raw = max(candidates, key=lambda x: x[0])
            home_map[pos] = _normalize_team(home_raw)

    # Step 3: rescue orphan home-VS pairs at the document tail (CANADÁ VS case).
    # Run BEFORE the caps_lines fallback so the authoritative orphan wins.
    # In a 2-column PDF, the left column's home team for an early CASILLERO can
    # appear after ALL CASILLERO markers in the extracted text.
    if casillero_spans:
        tail_start = casillero_spans[-1][2]
        tail = text[tail_start:]
        orphans = [
            _ms_clean(m.group(1))
            for m in re.finditer(
                r"([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ.\s]{0,50}?)\s*\n?\s*VS\b",
                tail,
            )
            if _ms_clean(m.group(1)) not in {"LOCAL", "VISITANTE"}
        ]
        missing = [pos for _, pos, _ in casillero_spans if pos not in home_map]
        for orphan, missing_pos in zip(orphans, missing):
            home_map[missing_pos] = _normalize_team(orphan)

    # Step 4: for positions still missing a home team, look for the last all-caps
    # line in the text window just before the CASILLERO (handles "PAÍSES BAJOS"
    # which appears without a VS marker in the 2-column layout).
    # Short acronyms (UEFA, FIFA, CAF…) are excluded to avoid matching
    # competition names embedded in the description text.
    _NOISE_TOKENS = frozenset(
        {"UEFA", "FIFA", "CONMEBOL", "CONCACAF", "AFC", "CAF", "OFC", "COPA", "VS"}
    )
    for i, (cas_start, pos, _) in enumerate(casillero_spans):
        if pos in home_map:
            continue
        prev_end = casillero_spans[i - 1][2] if i > 0 else 0
        window = text[prev_end:cas_start]
        caps_lines = [
            raw_line.strip()
            for raw_line in window.split("\n")
            if re.match(r"^[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ\s.]{2,50}$", raw_line.strip())
            and raw_line.strip() not in {"LOCAL", "VISITANTE", "LOCAL VISITANTE"}
            and raw_line.strip() not in _NOISE_TOKENS
        ]
        if caps_lines:
            home_map[pos] = _normalize_team(caps_lines[-1])

    fixtures: list[_Fixture] = []
    for _, pos, _ in casillero_spans:
        home = home_map.get(pos)
        away = away_map.get(pos)
        if home and away:
            fixtures.append(_Fixture(position=pos, home=home, away=away))

    closes_at = _parse_ms_cierre(text, draw_code)
    return draw_code, fixtures, closes_at


def ms_block_diagnostics(
    text: str, draw_code: str | None, candidates: list[dict[str, object]]
) -> dict[str, object]:
    """Summarise WHY a cierre was accepted/rejected for this concurso.

    Surfaces the fixture draw_code, and — when the only cierre block belongs to
    a DIFFERENT concurso (the PGM-802 stale-source case) — the rejected block's
    concurso + printed year, plus a human reason. No date is ever invented.
    """
    accepted = [c for c in candidates if c.get("matches_draw_code")]
    rejected = [c for c in candidates if not c.get("matches_draw_code")]
    diag: dict[str, object] = {
        "fixture_draw_code": draw_code,
        "cierre_block_found": bool(candidates),
        "accepted_close_block": bool(accepted),
        "rejected_close_block_draw_code": rejected[0]["block_concurso"] if rejected else None,
        "rejected_close_year": rejected[0]["year"] if rejected else None,
    }
    if accepted:
        diag["reason"] = f"cierre válido del concurso {draw_code} extraído del PDF"
    elif rejected:
        diag["reason"] = (
            f"PDF oficial contiene fixtures {draw_code}, pero no cierre válido {draw_code}; "
            f"bloque de cierre detectado pertenece al concurso {rejected[0]['block_concurso']}"
        )
    else:
        diag["reason"] = "el PDF no contiene un bloque de CIERRE DE VENTA legible"
    return diag


def ms_date_candidates(text: str, draw_code: str | None = None) -> list[dict[str, object]]:
    """Dump every cierre/venta date block found in the MS guide text.

    Each candidate carries its raw text, nearby context, inferred type, the
    block's own concurso, whether it matches ``draw_code``, and a confidence
    score. Used by the connector payload + discovery diagnostics so an operator
    can see exactly why a date was accepted or rejected (no silent guessing).
    """
    candidates: list[dict[str, object]] = []
    for match in _MS_VENTA_RE.finditer(text):
        block_concurso = match.group("concurso")
        matches_draw = draw_code is None or block_concurso == str(draw_code)
        month = _SPANISH_MONTHS.get((match.group("month") or "").lower())
        year = match.group("year")
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 40)
        # Confidence: a cierre block whose concurso matches the fixtures and
        # that prints its own year is high; a mismatch is the stale-source tell.
        if not matches_draw:
            confidence = "low"
        elif year is None:
            confidence = "medium"  # year inferred, not printed
        else:
            confidence = "high"
        candidates.append(
            {
                "raw": match.group(0).replace("\n", " ").strip(),
                "context": text[start:end].replace("\n", " ").strip(),
                "type": "cierre_de_venta",
                "block_concurso": block_concurso,
                "matches_draw_code": matches_draw,
                "day": match.group("day"),
                "month": month,
                "year": year,
                "confidence": confidence,
            }
        )
    return candidates


def _parse_ms_cierre(text: str, draw_code: str | None = None) -> datetime | None:
    """Parse the MS cierre de venta.

    Returns the registration-close datetime ONLY when the cierre block's own
    "Concurso N" matches the guide's ``draw_code``. A mismatch (stale cierre
    block — the PGM-802 case) returns None so the slate is left without a date
    rather than activated on a wrong/old one.
    """
    match = _MS_VENTA_RE.search(text)
    if match is None:
        return None
    block_concurso = match.group("concurso")
    if draw_code is not None and block_concurso is not None and block_concurso != str(draw_code):
        # Cierre block belongs to a different concurso than the fixtures —
        # the guide PDF is internally stale. Refuse to use this date.
        return None
    try:
        day = int(match.group("day"))
        month = _SPANISH_MONTHS.get(match.group("month").lower())
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
    except (TypeError, ValueError):
        return None
    if month is None:
        return None
    # Prefer the year printed on the guide; fall back to the current year only
    # when the document carries none. Never silently rewrite a printed year.
    year = int(match.group("year")) if match.group("year") else datetime.now(timezone.utc).year
    mx_tz = timezone(timedelta(hours=-6))
    local_dt = datetime(year, month, day, hour, minute, tzinfo=mx_tz)
    return local_dt.astimezone(timezone.utc)


class ProgolMsGuiaPdfConnector(SourceConnector):
    """LN Progol Media Semana PDF guide — 9-fixture MS contest.

    Fetches the LN /ProgolMediaSemana/Quiniela landing page to resolve the
    versioned guiamedia.pdf URL, then parses it with parse_ms_guia_text().
    """

    DEFAULT_LANDING_URL = "https://www.loterianacional.gob.mx/ProgolMediaSemana/Quiniela"

    def __init__(self, name: str, base_url: str | None = None) -> None:
        self.name = name
        self.kind = "progol_ms_guia_pdf"
        self.base_url = base_url or self.DEFAULT_LANDING_URL
        self.week_type = "midweek"
        self.description = "LN Progol Media Semana PDF guide — official upcoming MS contest source."

    def fetch(self) -> list[SourceDocument]:
        captured = datetime.now(timezone.utc)
        pdf_url = self._resolve_pdf_url()
        pdf_bytes, meta = self._download_with_meta(pdf_url)
        text = self._extract_text(pdf_bytes)
        draw_code, fixtures, closes_at = parse_ms_guia_text(text)
        date_candidates = ms_date_candidates(text, draw_code)
        block_diag = ms_block_diagnostics(text, draw_code, date_candidates)
        # Extraction confidence: high only when we accepted a date AND a
        # candidate's cierre block matched this concurso; otherwise low (stale
        # block / no date) so downstream gating can refuse activation.
        extraction_confidence = (
            "high"
            if closes_at is not None and any(c["matches_draw_code"] for c in date_candidates)
            else "low"
        )
        return [
            SourceDocument(
                source_name=self.name,
                source_url=str(meta.get("source_url") or pdf_url),
                captured_at=captured,
                payload={
                    "title": f"Progol MS Guía concurso {draw_code}" if draw_code else "Progol MS Guía",
                    "summary": f"Concurso MS {draw_code}, {len(fixtures)} fixtures parsed.",
                    "draw_code": draw_code,
                    "week_type": "midweek",
                    "registration_closes_at": closes_at.isoformat() if closes_at else None,
                    "date_candidates": date_candidates,
                    "block_diagnostics": block_diag,
                    "extraction_confidence": extraction_confidence,
                    "match_count": len(fixtures),
                    "fixtures": [
                        {"position": f.position, "home": f.home, "away": f.away}
                        for f in fixtures
                    ],
                    # PDF provenance (source of truth, auditable).
                    "source_url": meta.get("source_url"),
                    "pdf_sha256": meta.get("pdf_sha256"),
                    "content_length": meta.get("content_length"),
                    "etag": meta.get("etag"),
                    "last_modified": meta.get("last_modified"),
                    "fetched_at": captured.isoformat(),
                    "raw_text_excerpt": text[:600],
                },
            )
        ]

    def _resolve_pdf_url(self) -> str:
        try:
            request = Request(
                self.base_url,
                headers={"User-Agent": "proAI/0.1 (+https://local.proai)", "Accept": "text/html"},
            )
            with urlopen(request, timeout=15) as response:
                html = response.read().decode("utf-8", errors="replace")
        except Exception:
            return _MS_FALLBACK_URL
        m = _MS_GUIA_PDF_HREF_RE.search(html)
        if m is None:
            return _MS_FALLBACK_URL
        return urljoin(self.base_url, m.group(1))

    def _download_bytes(self, url: str) -> bytes:
        request = Request(
            url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)", "Accept": "application/pdf"},
        )
        with urlopen(request, timeout=30) as response:
            return response.read()

    def _download_with_meta(self, url: str) -> tuple[bytes, dict[str, object]]:
        """Download the PDF and capture provenance so a stale/cached document
        is auditable: final URL (after redirects), sha256, length, and the
        ETag/Last-Modified the origin reports. The PDF bytes are the source of
        truth; this metadata lets us prove WHICH bytes we parsed."""
        request = Request(
            url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)", "Accept": "application/pdf"},
        )
        with urlopen(request, timeout=30) as response:
            body = response.read()
            try:
                final_url = response.geturl()
            except Exception:
                final_url = url
            headers = getattr(response, "headers", None)
            etag = headers.get("ETag") if headers is not None else None
            last_modified = headers.get("Last-Modified") if headers is not None else None
        meta: dict[str, object] = {
            "source_url": final_url,
            "pdf_sha256": hashlib.sha256(body).hexdigest(),
            "content_length": len(body),
            "etag": etag,
            "last_modified": last_modified,
        }
        return body, meta

    def _extract_text(self, pdf_bytes: bytes) -> str:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
