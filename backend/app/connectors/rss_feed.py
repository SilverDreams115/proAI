from __future__ import annotations

from datetime import datetime, timezone
from urllib.request import Request
from xml.etree import ElementTree

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class RssFeedConnector(SourceConnector):
    """Fetches an RSS feed and emits one document per item."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "rss_feed"
        self.base_url = base_url
        self.description = "Fetches an RSS feed and extracts items into source documents."

    def fetch(self) -> list[SourceDocument]:
        request = Request(
            self.base_url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)"},
        )
        with urlopen(request, timeout=15) as response:
            xml_bytes = response.read()

        root = ElementTree.fromstring(xml_bytes)
        items = root.findall(".//item")
        documents: list[SourceDocument] = []
        for item in items:
            title = (item.findtext("title") or self.name).strip()
            summary = (item.findtext("description") or title).strip()
            link = (item.findtext("link") or self.base_url).strip()
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=link,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": title,
                        "summary": summary,
                        "headings": [title],
                        "teams": [],
                        "team_stats": [],
                        "match_stats": [],
                        "historical_results": [],
                        "availability_reports": [],
                    },
                )
            )
        return documents
