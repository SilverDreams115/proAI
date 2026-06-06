from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class _CatalogHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_tag: str | None = None
        self._current_href: str | None = None
        self.title = ""
        self.headings: list[str] = []
        self.paragraphs: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self._current_tag = tag.lower()
        if self._current_tag == "a":
            attr_map = dict(attrs)
            self._current_href = attr_map.get("href")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        normalized = tag.lower()
        if normalized == "a":
            self._current_href = None
        if self._current_tag == normalized:
            self._current_tag = None

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        text = " ".join(data.split())
        if not text:
            return
        if self._current_tag == "title" and not self.title:
            self.title = text
        if self._current_tag in {"h1", "h2", "h3", "h4"}:
            self.headings.append(text)
        if self._current_tag in {"p", "li", "span"}:
            self.paragraphs.append(text)
        if self._current_tag == "a" and self._current_href:
            self.links.append((text, self._current_href))


class ProgolCatalogHtmlConnector(SourceConnector):
    """Fetches public Progol catalog/product pages and extracts contest metadata."""

    def __init__(self, name: str, base_url: str, contest_type: str = "progol") -> None:
        self.name = name
        self.kind = "progol_catalog_html"
        self.base_url = base_url
        self.contest_type = contest_type
        self.description = "Fetches Progol catalog pages and extracts contest structure and available options."

    def fetch(self) -> list[SourceDocument]:
        request = Request(
            self.base_url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)"},
        )
        with urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")

        parser = _CatalogHtmlParser()
        parser.feed(html)

        title = parser.title or self.name
        text_blob = " ".join(parser.headings + parser.paragraphs)
        match_count = self._extract_match_count(text_blob)
        draw_number = self._extract_draw_number(text_blob, parser.links)
        option_labels = self._extract_option_labels(text_blob)
        sale_window = self._extract_sale_window(text_blob)
        official_link = self._extract_official_link(parser.links)

        return [
            SourceDocument(
                source_name=self.name,
                source_url=self.base_url,
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": title,
                    "summary": self._build_summary(match_count, option_labels, sale_window),
                    "headings": parser.headings[:8],
                    "teams": [],
                    "competition": None,
                    "team_stats": [],
                    "match_stats": [],
                    "historical_results": [],
                    "availability_reports": [],
                    "catalog_metadata": {
                        "contest_type": self.contest_type,
                        "draw_number": draw_number,
                        "match_count": match_count,
                        "option_labels": option_labels,
                        "sale_window": sale_window,
                        "official_quiniela_link": official_link,
                        "source_links": [
                            {"label": label, "url": url}
                            for label, url in parser.links[:15]
                        ],
                    },
                },
            )
        ]

    def _extract_match_count(self, text: str) -> int | None:
        normalized = text.lower()
        patterns = [
            r"quiniela de\s+(\d+)\s+partidos",
            r"lo conforman\s+(\d+)\s+partidos",
            r"se trata de una quiniela de\s+(\d+)\s+partidos",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return int(match.group(1))
        return None

    def _extract_draw_number(self, text: str, links: list[tuple[str, str]]) -> int | None:
        normalized = " ".join(text.split()).lower()
        patterns = [
            r"(?:quiniela|concurso)\s*(?:no\.?|numero|número)?\s*(\d{3,5})",
            r"progol\s+(?:1/2\s+semana|media\s+semana|revancha)?\s*(\d{3,5})",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        for label, _url in links:
            label_text = label.strip()
            if re.fullmatch(r"\d{3,5}", label_text):
                return int(label_text)
        return None

    def _extract_option_labels(self, text: str) -> list[str]:
        normalized = text.lower()
        options: list[str] = []
        if "local" in normalized:
            options.append("local")
        if "empate" in normalized:
            options.append("empate")
        if "visitante" in normalized or "visita" in normalized:
            options.append("visitante")
        return options

    def _extract_sale_window(self, text: str) -> str | None:
        normalized = " ".join(text.split())
        match = re.search(r"(La venta de[^.]+\.)", normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_official_link(self, links: list[tuple[str, str]]) -> str | None:
        for label, url in links:
            lowered = label.lower()
            if "quiniela de la semana" in lowered or "quiniela" in lowered:
                return url
        return None

    def _build_summary(
        self,
        match_count: int | None,
        option_labels: list[str],
        sale_window: str | None,
    ) -> str:
        parts = [f"Contest type: {self.contest_type}."]
        if match_count is not None:
            parts.append(f"Matches in slate: {match_count}.")
        if option_labels:
            parts.append(f"Options: {', '.join(option_labels)}.")
        if sale_window:
            parts.append(sale_window)
        return " ".join(parts)
