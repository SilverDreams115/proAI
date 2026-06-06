from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class JsonFeedConnector(SourceConnector):
    """Fetches a JSON feed and returns one source document per top-level item."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "json_feed"
        self.base_url = base_url
        self.description = "Fetches a JSON feed and emits normalized source documents."

    def fetch(self) -> list[SourceDocument]:
        request = Request(
            self.base_url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)"},
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))

        items = payload if isinstance(payload, list) else payload.get("items", [])
        documents: list[SourceDocument] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", self.name))
            summary = str(item.get("summary", title))
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=str(item.get("url", self.base_url)),
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": title,
                        "summary": summary,
                        "headings": item.get("headings", []),
                        "teams": item.get("teams", []),
                        "competition": item.get("competition"),
                        "team_stats": item.get("team_stats", []),
                        "match_stats": item.get("match_stats", []),
                        "historical_results": item.get("historical_results", []),
                        "fixture_candidates": item.get("fixture_candidates", item.get("fixtures", [])),
                        "availability_reports": item.get("availability_reports", []),
                        "catalog_metadata": item.get("catalog_metadata", {}),
                    },
                )
            )
        return documents
