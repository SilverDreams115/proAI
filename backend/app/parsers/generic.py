from __future__ import annotations

from typing import Any

from app.parsers.base import SourceParser


class GenericSourceParser(SourceParser):
    profile_name = "generic"

    def parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": payload.get("title", ""),
            "summary": payload.get("summary", ""),
            "headings": payload.get("headings", []),
            "teams": payload.get("teams", []),
            "competition": payload.get("competition"),
            "team_stats": payload.get("team_stats", []),
            "match_stats": payload.get("match_stats", []),
            "historical_results": payload.get("historical_results", []),
            "fixture_candidates": payload.get("fixture_candidates", payload.get("fixtures", [])),
            "availability_reports": payload.get("availability_reports", []),
            "catalog_metadata": payload.get("catalog_metadata", {}),
            "context_summary": payload.get("context_summary", ""),
            "article_prediction": payload.get("article_prediction"),
        }
