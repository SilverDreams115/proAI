"""Progol official-results connector + parser (Lotería Nacional).

Lotería Nacional publishes the official marcadores for each Progol /
Progol Media Semana concurso. This module fetches that results document
and parses it into per-casillero rows that the ResultsIngestionService
maps to slate matches (strictly by draw_code + position — never a fuzzy
team match) and feeds to ``LiveResultService``.

The parser accepts the two encodings LN/operators use:

* score line:  ``1  MÉXICO 2-1 SUDÁFRICA  FINAL``
* sign line:   ``1  L``   (L/E/V or 1/X/2 when only the sign is published)

A line with no score and no sign (``PENDIENTE`` / not started) yields a
row with ``status=scheduled`` and no goals — so the caller can skip it
rather than invent a result. ``EN VIVO`` / ``MEDIO TIEMPO`` markers (and
an optional minute) flag live rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any

from app.connectors.base import SourceConnector, SourceDocument
from app.connectors.http import safe_urlopen as urlopen
from app.domain.entities import MatchResultStatus

_CONCURSO_RE = re.compile(r"CONCURSO\s+(\d{3,5})", flags=re.IGNORECASE)

# "1  MÉXICO 2-1 SUDÁFRICA  FINAL 90'"  → pos, home, gh, ga, away, [status], [min]
_SCORE_RE = re.compile(
    r"^\s*(?P<pos>\d{1,2})[\.\)]?\s+"
    r"(?P<home>.+?)\s+(?P<gh>\d{1,2})\s*[-:]\s*(?P<ga>\d{1,2})\s+(?P<away>.+?)"
    r"(?:\s+(?P<status>FINAL(?:IZADO)?|JUGADO|EN\s+VIVO|VIVO|MEDIO\s+TIEMPO|DESCANSO|ENTRETIEMPO))?"
    r"(?:\s+(?P<minute>\d{1,3})\s*'?)?\s*$",
    flags=re.IGNORECASE,
)
# "1  L" / "1 X" / "13 2"  → pos, sign
_SIGN_RE = re.compile(r"^\s*(?P<pos>\d{1,2})[\.\)]?\s+(?P<sign>[LEV12X])\s*$", flags=re.IGNORECASE)
_HTML_COMBO_ROW_RE = re.compile(
    r"<tr[^>]*>\s*"
    r"<td[^>]*>\s*(?P<draw>\d{3,5})\s*</td>\s*"
    r"<td[^>]*>\s*(?P<date>[^<]*)\s*</td>\s*"
    r"<td[^>]*>\s*(?P<combo>[LEV12X\s]+)\s*</td>\s*"
    r"</tr>",
    flags=re.IGNORECASE | re.DOTALL,
)
# "14 GHANA vs PANAMA PENDIENTE"  → pos, home, away (no score yet)
_PENDING_RE = re.compile(
    r"^\s*(?P<pos>\d{1,2})[\.\)]?\s+(?P<home>.+?)\s+(?:VS|V\.S\.?|-)\s+(?P<away>.+?)"
    r"(?:\s+(?P<status>PENDIENTE|PROGRAMADO|NO\s+INICIADO|POR\s+JUGAR))?\s*$",
    flags=re.IGNORECASE,
)

_SIGN_TO_CODE = {"L": "1", "E": "X", "V": "2", "1": "1", "X": "X", "2": "2"}
_LIVE_WORDS = ("EN VIVO", "VIVO")
_HALFTIME_WORDS = ("MEDIO TIEMPO", "DESCANSO", "ENTRETIEMPO")
_FINAL_WORDS = ("FINAL", "FINALIZADO", "JUGADO")


@dataclass(frozen=True)
class ResultLine:
    position: int
    home: str | None
    away: str | None
    home_goals: int | None
    away_goals: int | None
    result_code: str | None
    status: MatchResultStatus
    minute: int | None
    is_final: bool


def _status_from_keyword(keyword: str | None, *, has_score: bool) -> MatchResultStatus:
    upper = (keyword or "").upper().strip()
    if any(word in upper for word in _HALFTIME_WORDS):
        return MatchResultStatus.HALFTIME
    if any(word in upper for word in _LIVE_WORDS):
        return MatchResultStatus.LIVE
    if any(word in upper for word in _FINAL_WORDS):
        return MatchResultStatus.FULL_TIME
    # A bare published score (no live/halftime marker) is a final result;
    # an official results doc only lists scores for played matches.
    return MatchResultStatus.FULL_TIME if has_score else MatchResultStatus.SCHEDULED


def _result_code(gh: int | None, ga: int | None) -> str | None:
    if gh is None or ga is None:
        return None
    if gh > ga:
        return "1"
    if gh < ga:
        return "2"
    return "X"


def parse_progol_resultados_text(text: str) -> tuple[str | None, list[ResultLine]]:
    """Extract (draw_code, result_lines) from an LN results document.

    Public so tests can exercise the parser against captured text without
    round-tripping bytes. Lines that match neither a score nor a sign are
    ignored (headers, legends, revancha duplicates after position 14).
    """
    draw_match = _CONCURSO_RE.search(text)
    draw_code = draw_match.group(1) if draw_match else None

    rows: list[ResultLine] = []
    seen: set[int] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        score = _SCORE_RE.match(line)
        if score is not None:
            position = int(score.group("pos"))
            if position in seen or not 1 <= position <= 14:
                continue
            gh = int(score.group("gh"))
            ga = int(score.group("ga"))
            status = _status_from_keyword(score.group("status"), has_score=True)
            is_final = status == MatchResultStatus.FULL_TIME
            seen.add(position)
            rows.append(
                ResultLine(
                    position=position,
                    home=_clean(score.group("home")),
                    away=_clean(score.group("away")),
                    home_goals=gh,
                    away_goals=ga,
                    result_code=_result_code(gh, ga),
                    status=status,
                    minute=int(score.group("minute")) if score.group("minute") else None,
                    is_final=is_final,
                )
            )
            continue
        pending = _PENDING_RE.match(line)
        if pending is not None:
            position = int(pending.group("pos"))
            if position in seen or not 1 <= position <= 14:
                continue
            seen.add(position)
            rows.append(
                ResultLine(
                    position=position,
                    home=_clean(pending.group("home")),
                    away=_clean(pending.group("away")),
                    home_goals=None,
                    away_goals=None,
                    result_code=None,
                    status=MatchResultStatus.SCHEDULED,
                    minute=None,
                    is_final=False,
                )
            )
            continue
        sign = _SIGN_RE.match(line)
        if sign is not None:
            position = int(sign.group("pos"))
            if position in seen or not 1 <= position <= 14:
                continue
            code = _SIGN_TO_CODE.get(sign.group("sign").upper())
            seen.add(position)
            rows.append(
                ResultLine(
                    position=position,
                    home=None,
                    away=None,
                    home_goals=None,
                    away_goals=None,
                    result_code=code,
                    status=MatchResultStatus.FULL_TIME,
                    minute=None,
                    is_final=True,
                )
            )
    rows.sort(key=lambda r: r.position)
    if not rows:
        return _parse_html_historical_combo(text)
    return draw_code, rows


def _parse_html_historical_combo(text: str) -> tuple[str | None, list[ResultLine]]:
    """Parse LN's current HTML historical table.

    LN's live results page currently publishes the latest official result as a
    table row: Sorteo / Fecha / Combinación Ganadora. The combination is
    sign-only (L/E/V), so we record final outcomes without inventing scorelines.
    """
    for match in _HTML_COMBO_ROW_RE.finditer(text):
        signs = [
            _SIGN_TO_CODE[token.upper()]
            for token in unescape(match.group("combo")).split()
            if token.upper() in _SIGN_TO_CODE
        ]
        if len(signs) not in {7, 9, 14}:
            continue
        rows = [
            ResultLine(
                position=i,
                home=None,
                away=None,
                home_goals=None,
                away_goals=None,
                result_code=code,
                status=MatchResultStatus.FULL_TIME,
                minute=None,
                is_final=True,
            )
            for i, code in enumerate(signs, start=1)
        ]
        return match.group("draw"), rows
    return None, []


def _clean(raw: str) -> str:
    return " ".join(raw.split()).strip(".,;:- ")


class ProgolResultadosConnector(SourceConnector):
    """Fetch the LN Progol official-results document for a concurso."""

    kind = "progol_resultados"
    description = "Lotería Nacional Progol official results (marcadores)."
    DEFAULT_RESULTS_URL = "https://www.loterianacional.gob.mx/Progol/Resultados"
    MEDIA_SEMANA_RESULTS_URL = "https://www.loterianacional.gob.mx/ProgolMediaSemana/Resultados"

    def __init__(self, name: str = "LN Progol Resultados", base_url: str | None = None) -> None:
        self.name = name
        self.base_url = base_url or self.DEFAULT_RESULTS_URL

    def fetch(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        for url in self._urls_to_fetch():
            text = self._download_text(url)
            draw_code, rows = parse_progol_resultados_text(text)
            captured_at = datetime.now(timezone.utc)
            payload: dict[str, Any] = {
                "draw_code": draw_code,
                "raw_text": text,
                "result_count": len(rows),
            }
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=url,
                    captured_at=captured_at,
                    payload=payload,
                )
            )
        return documents

    def _urls_to_fetch(self) -> list[str]:
        urls = [self.base_url]
        if self.base_url.rstrip("/") == self.DEFAULT_RESULTS_URL:
            urls.append(self.MEDIA_SEMANA_RESULTS_URL)
        return urls

    @staticmethod
    def _download_text(url: str) -> str:
        from urllib.request import Request

        request = Request(url, headers={"User-Agent": "proAI/0.1 (+https://local.proai)"})
        with urlopen(request, timeout=30) as response:
            body = response.read()
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)
