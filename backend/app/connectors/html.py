from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request
from zoneinfo import ZoneInfo

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class _HtmlSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_tag: str | None = None
        self.title = ""
        self.headings: list[str] = []
        self.texts: list[str] = []
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self._current_tag = tag.lower()

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if self._current_tag == tag.lower():
            self._current_tag = None

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        text = " ".join(data.split())
        if not text:
            return
        self.texts.append(text)
        if self._current_tag == "title" and not self.title:
            self.title = text
        if self._current_tag in {"h1", "h2", "h3", "h4"}:
            self.headings.append(text)
        if self._current_tag in {"h1", "h2", "h3", "h4", "p", "li"}:
            self.blocks.append((self._current_tag, text))


class GenericHtmlConnector(SourceConnector):
    """Fetch a public HTML page and extract lightweight structured signals."""

    _month_numbers = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }
    _section_pattern = re.compile(
        r"pr[oó]ximos partidos del\s+"
        r"(?P<start_day>\d{1,2})\s+al\s+(?P<end_day>\d{1,2})\s+de\s+"
        r"(?P<month>[a-záéíóú]+)\s+del\s+(?P<year>\d{4})\s+\|\|\s+"
        r"quiniela\s+no\s+(?P<draw_number>\d{3,5})",
        re.IGNORECASE,
    )

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "html_page"
        self.base_url = base_url
        self.description = "Fetches an HTML page and extracts the title and top headings."

    def fetch(self) -> list[SourceDocument]:
        request = Request(
            self.base_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 proAI/0.1"
                )
            },
        )
        with urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")

        parser = _HtmlSummaryParser()
        parser.feed(html)
        title = parser.title or self.name
        headings = parser.headings[:5]
        summary = " | ".join(headings) if headings else title
        context_documents = self._extract_progol_analysis_documents(parser, title)
        if context_documents:
            return context_documents

        payload: dict[str, object] = {
            "title": title,
            "headings": headings,
            "summary": summary,
        }
        payload.update(self._extract_progol_live_payload(parser.texts))
        payload.update(self._extract_resultados_ya_recent_payload(parser.texts))

        return [
            SourceDocument(
                source_name=self.name,
                source_url=self.base_url,
                captured_at=datetime.now(timezone.utc),
                payload=payload,
            )
        ]

    def _extract_progol_analysis_documents(
        self,
        parser: _HtmlSummaryParser,
        page_title: str,
    ) -> list[SourceDocument]:
        if not any("progol" in item.lower() for item in [page_title, *parser.headings]):
            return []

        captured_at = datetime.now(timezone.utc)
        documents: list[SourceDocument] = []
        blocks = parser.blocks
        index = 0
        while index < len(blocks):
            tag, text = blocks[index]
            teams = self._parse_match_heading(text)
            if tag not in {"h2", "h3", "h4"} or teams is None:
                index += 1
                continue

            body: list[str] = []
            next_index = index + 1
            while next_index < len(blocks):
                next_tag, next_text = blocks[next_index]
                if next_tag in {"h2", "h3", "h4"} and self._parse_match_heading(next_text) is not None:
                    break
                if next_tag in {"p", "li"}:
                    body.append(next_text)
                next_index += 1

            summary = self._context_summary(page_title, text, body)
            prediction = self._extract_article_prediction(body)
            home_team, away_team = teams
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=captured_at,
                    payload={
                        "title": text,
                        "summary": summary,
                        "headings": [page_title, text],
                        "teams": [home_team, away_team],
                        "competition": "Progol Media Semana",
                        "team_stats": [],
                        "match_stats": [],
                        "historical_results": [],
                        "fixture_candidates": [],
                        "availability_reports": [],
                        "context_summary": summary,
                        "article_prediction": prediction,
                    },
                )
            )
            index = next_index
        return documents

    def _parse_match_heading(self, text: str) -> tuple[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if "|" in normalized or len(normalized) > 120:
            return None
        match = re.fullmatch(r"(.+?)\s+vs\.?\s+(.+)", normalized, flags=re.IGNORECASE)
        if match is None:
            return None
        home_team = self._clean_team_name(match.group(1))
        away_team = self._clean_team_name(match.group(2))
        if not home_team or not away_team:
            return None
        return home_team, away_team

    def _context_summary(self, page_title: str, heading: str, body: list[str]) -> str:
        context = " ".join(body[:6])
        context = re.sub(r"\s+", " ", context).strip()
        prefix = f"{page_title}. {heading}."
        if not context:
            return prefix
        return f"{prefix} {context}"

    def _extract_article_prediction(self, body: list[str]) -> str | None:
        text = " ".join(body)
        match = re.search(r"predicci[oó]n:\s*(local|empate|visitante|l|e|v)\b", text, flags=re.IGNORECASE)
        if match is None:
            return None
        raw = match.group(1).lower()
        return {"local": "L", "empate": "E", "visitante": "V"}.get(raw, raw.upper())

    def _extract_progol_live_payload(self, text_items: list[str]) -> dict[str, object]:
        text = " ".join(text_items)
        sections = list(self._section_pattern.finditer(text))
        if not sections:
            return {}

        extracted_sections: list[dict[str, object]] = []
        for index, section in enumerate(sections):
            body_start = section.end()
            body_end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            body = text[body_start:body_end]
            fixtures = self._extract_progol_rows(
                body,
                year=int(section.group("year")),
                fallback_day=int(section.group("start_day")),
                fallback_month=section.group("month"),
            )
            if not fixtures:
                continue
            extracted_sections.append(
                {
                    "draw_number": int(section.group("draw_number")),
                    "start_day": int(section.group("start_day")),
                    "end_day": int(section.group("end_day")),
                    "month": section.group("month"),
                    "year": int(section.group("year")),
                    "fixtures": fixtures,
                }
            )

        if not extracted_sections:
            return {}

        current = max(extracted_sections, key=lambda item: int(item["draw_number"]))  # type: ignore[call-overload,arg-type]
        fixtures = list(current["fixtures"])  # type: ignore[call-overload,arg-type]
        draw_number = int(current["draw_number"])  # type: ignore[call-overload,arg-type]
        return {
            "fixture_candidates": fixtures,
            "catalog_metadata": {
                "contest_type": "progol_media_semana",
                "draw_number": draw_number,
                "match_count": len(fixtures),
            },
        }

    def _extract_resultados_ya_recent_payload(self, text_items: list[str]) -> dict[str, object]:
        text = " ".join(text_items)
        marker = re.search(r"partidos recientes", text, flags=re.IGNORECASE)
        if marker is None:
            return {}
        end_match = re.search(r"proximos partidos|pr[oó]ximos partidos|reva\s+\|\|", text[marker.end():], flags=re.IGNORECASE)
        body_end = marker.end() + end_match.start() if end_match else len(text)
        body = text[marker.end():body_end]
        results = self._extract_resultados_ya_recent_rows(body)
        if not results:
            return {}
        return {"historical_results": results}

    def _extract_resultados_ya_recent_rows(self, body: str) -> list[dict[str, object]]:
        month_pattern = "|".join(sorted(self._month_numbers, key=len, reverse=True))
        row_pattern = re.compile(
            rf"(?P<position>\d{{1,2}})\s+"
            rf"(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\s+-\s+"
            rf"(?P<day>\d{{1,2}})\s+(?P<month>{month_pattern})\s+"
            rf"(?P<home>.+?)\s+(?P<home_goals>\d+)-(?P<away_goals>\d+)\s+"
            rf"(?P<away>.+?)\s+Final"
            rf"(?=\s+\d{{1,2}}\s+(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\s+-\s+|\s*$)",
            re.IGNORECASE | re.DOTALL,
        )
        year = datetime.now(ZoneInfo("America/Mexico_City")).year
        results: list[dict[str, object]] = []
        for match in row_pattern.finditer(body):
            home_team = self._clean_team_name(match.group("home"))
            away_team = self._clean_team_name(match.group("away"))
            if not home_team or not away_team:
                continue
            played_at = self._build_mexico_city_kickoff(
                year=year,
                month_name=match.group("month"),
                day=int(match.group("day")),
                time_text=None,
            )
            results.append(
                {
                    "competition_name": "Resultados Ya Recent Form",
                    "home_team": home_team,
                    "away_team": away_team,
                    "played_at": played_at,
                    "home_goals": int(match.group("home_goals")),
                    "away_goals": int(match.group("away_goals")),
                }
            )
        return results

    def _extract_progol_rows(
        self,
        body: str,
        *,
        year: int,
        fallback_day: int,
        fallback_month: str,
    ) -> list[dict[str, object]]:
        month_pattern = "|".join(sorted(self._month_numbers, key=len, reverse=True))
        row_pattern = re.compile(
            rf"(?P<position>\d{{1,2}})\s+"
            rf"(?:(?P<day>\d{{1,2}})\s+)?(?P<month>{month_pattern})\s+"
            rf"(?P<home>.+?)\s+VS\s+(?P<away>.+?)"
            rf"(?=\s+\d{{1,2}}\s+(?:\d{{1,2}}\s+)?(?:{month_pattern})\s+|\s+Horarios|$)",
            re.IGNORECASE | re.DOTALL,
        )
        fixtures: list[dict[str, object]] = []
        for match in row_pattern.finditer(body):
            home_team = self._clean_team_name(match.group("home"))
            away_team, time_text = self._clean_away_team_and_time(match.group("away"))
            if not home_team or not away_team:
                continue
            month_name = match.group("month") or fallback_month
            kickoff_at = self._build_mexico_city_kickoff(
                year=year,
                month_name=month_name,
                day=int(match.group("day") or fallback_day),
                time_text=time_text,
            )
            fixtures.append(
                {
                    "position": int(match.group("position")),
                    "competition": "Progol Media Semana",
                    "country": "Global",
                    "season": str(year),
                    "home_team": home_team,
                    "away_team": away_team,
                    "kickoff_at": kickoff_at,
                    "venue": None,
                }
            )
        return sorted(fixtures, key=lambda item: int(item["position"]))  # type: ignore[call-overload,arg-type]

    def _clean_team_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" .")

    def _clean_away_team_and_time(self, value: str) -> tuple[str, str | None]:
        cleaned = re.sub(r"\s+", " ", value).strip(" .")
        cleaned = re.sub(r"\bFINAL\s*!!.*$", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\b\d+\s*-\s*\d+\b.*$", "", cleaned).strip()
        time_match = re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm))\b", cleaned, flags=re.IGNORECASE)
        time_text = time_match.group(1) if time_match else None
        if time_match:
            cleaned = cleaned[: time_match.start()].strip()
        cleaned = re.sub(r"\s+--\s*$", "", cleaned).strip(" .")
        return cleaned, time_text

    def _build_mexico_city_kickoff(
        self,
        *,
        year: int,
        month_name: str,
        day: int,
        time_text: str | None,
    ) -> str:
        month = self._month_numbers[month_name.lower()]
        hour = 12
        minute = 0
        if time_text:
            time_match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)", time_text.strip(), flags=re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                meridiem = time_match.group(3).lower()
                if meridiem == "pm" and hour != 12:
                    hour += 12
                if meridiem == "am" and hour == 12:
                    hour = 0
        local_dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/Mexico_City"))
        return local_dt.astimezone(timezone.utc).isoformat()
