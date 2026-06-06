from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class CsvFeedConnector(SourceConnector):
    """Fetches a CSV feed and converts rows into source documents."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "csv_feed"
        self.base_url = base_url
        self.description = "Fetches a CSV feed and maps rows into source documents."

    def fetch(self) -> list[SourceDocument]:
        request = Request(
            self.base_url,
            headers={"User-Agent": "proAI/0.1 (+https://local.proai)"},
        )
        with urlopen(request, timeout=15) as response:
            csv_text = response.read().decode("utf-8", errors="replace")

        reader = csv.DictReader(StringIO(csv_text))
        documents: list[SourceDocument] = []
        for row in reader:
            title = row.get("title") or row.get("match") or self.name
            summary = row.get("summary") or title
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=row.get("url") or self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": title,
                        "summary": summary,
                        "headings": [title],
                        "teams": [row.get("home_team"), row.get("away_team")],
                        "competition": row.get("competition"),
                        "team_stats": [],
                        "match_stats": [],
                        "historical_results": [],
                        "availability_reports": [],
                    },
                )
            )
        return documents
