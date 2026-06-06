from __future__ import annotations

from typing import Any

from app.parsers.base import SourceParser


class SportsFeedV1Parser(SourceParser):
    profile_name = "sports_feed_v1"

    def parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        fixtures = payload.get("fixtures", [])
        normalized_results = []
        fixture_candidates = []
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                continue
            fixture_candidates.append(
                {
                    "competition": fixture.get("competition"),
                    "competition_code": fixture.get("competition_code"),
                    "home_team": fixture.get("home_team"),
                    "away_team": fixture.get("away_team"),
                    "kickoff_at": fixture.get("kickoff_at") or fixture.get("played_at"),
                    "venue": fixture.get("venue"),
                    "country": fixture.get("country"),
                    "season": fixture.get("season"),
                    "status": fixture.get("status"),
                }
            )
            if fixture.get("home_goals") is not None and fixture.get("away_goals") is not None:
                normalized_results.append(
                    {
                        "competition_name": fixture.get("competition"),
                        "home_team": fixture.get("home_team"),
                        "away_team": fixture.get("away_team"),
                        "played_at": fixture.get("played_at") or fixture.get("kickoff_at"),
                        "home_goals": fixture.get("home_goals"),
                        "away_goals": fixture.get("away_goals"),
                    }
                )

        return {
            "title": payload.get("title", "sports-feed"),
            "summary": payload.get("summary", ""),
            "headings": payload.get("headings", []),
            "teams": payload.get("teams", []),
            "competition": payload.get("competition"),
            "team_stats": payload.get("team_stats", []),
            "match_stats": payload.get("match_stats", []),
            "historical_results": normalized_results,
            "fixture_candidates": payload.get("fixture_candidates", fixture_candidates),
            "availability_reports": payload.get("availability_reports", []),
            "catalog_metadata": payload.get("catalog_metadata", {}),
        }
