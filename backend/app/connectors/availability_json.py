from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class AvailabilityJsonConnector(SourceConnector):
    """Fetches structured availability feeds for injuries, suspensions, and lineups."""

    def __init__(self, name: str, base_url: str, feed_type: str = "availability") -> None:
        self.name = name
        self.kind = "availability_json_feed"
        self.base_url = base_url
        self.feed_type = feed_type
        self.description = (
            "Fetches structured JSON availability items for injuries, suspensions, and projected lineups."
        )

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
            competition = item.get("competition")
            availability_reports = item.get("availability_reports")
            if not isinstance(availability_reports, list):
                single_report = self._normalize_single_report(item)
                availability_reports = [single_report] if single_report is not None else []
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=str(item.get("url", self.base_url)),
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": title,
                        "summary": summary,
                        "headings": item.get("headings", [title]),
                        "teams": item.get("teams", []),
                        "competition": competition,
                        "team_stats": item.get("team_stats", []),
                        "match_stats": item.get("match_stats", []),
                        "historical_results": item.get("historical_results", []),
                        "availability_reports": availability_reports,
                    },
                )
            )
        return documents

    def _normalize_single_report(self, item: dict[str, Any]) -> dict[str, Any] | None:
        team_name = str(item.get("team_name", "")).strip()
        player_name = str(item.get("player_name", "")).strip()
        if not team_name or not player_name:
            return None
        return {
            "team_name": team_name,
            "player_name": player_name,
            "position": item.get("position"),
            "status": str(item.get("status", "doubtful")),
            "category": str(item.get("category", self.feed_type)),
            "detail": str(item.get("detail", item.get("summary", ""))),
            "confidence": float(item.get("confidence", 0.8)),
            "impact_score": float(item.get("impact_score", 0.5)),
        }
