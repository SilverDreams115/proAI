from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class FootballDataApiConnector(SourceConnector):
    """Connector for football-data.org v4 match feeds."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        competition_code: str,
        date_from: str | None = None,
        date_to: str | None = None,
        season: str | None = None,
    ) -> None:
        self.name = name
        self.kind = "football_data_api"
        self.base_url = base_url.rstrip("/")
        self.description = "football-data.org v4 competition matches feed."
        self.api_key = api_key
        self.competition_code = competition_code
        self.date_from = date_from
        self.date_to = date_to
        # `season` and `date_from/date_to` are mutually exclusive on the
        # football-data.org side. We surface both so the caller can pick
        # whichever matches the historical window they need.
        self.season = season

    def fetch(self) -> list[SourceDocument]:
        params = {}
        if self.date_from:
            params["dateFrom"] = self.date_from
        if self.date_to:
            params["dateTo"] = self.date_to
        if self.season:
            params["season"] = self.season
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.base_url}/v4/competitions/{self.competition_code}/matches{query}"
        request = Request(url, headers={"X-Auth-Token": self.api_key, "User-Agent": "proAI/0.1"})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))

        matches = payload.get("matches", [])
        documents: list[SourceDocument] = []
        for item in matches:
            home_team = item.get("homeTeam", {}).get("name")
            away_team = item.get("awayTeam", {}).get("name")
            competition_info = item.get("competition", {})
            competition = competition_info.get("name")
            competition_code = competition_info.get("code")
            kickoff_at = item.get("utcDate")
            score = item.get("score", {}).get("fullTime", {})
            # sports_feed_v1 parser expects goals embedded in the
            # `fixtures` list (it rebuilds `historical_results` from
            # there). Carrying score under a separate key would be
            # dropped on the way through the parser.
            fixture = {
                "competition": competition,
                "competition_code": competition_code,
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_at": kickoff_at,
                "played_at": kickoff_at,
                "venue": item.get("venue"),
                "status": item.get("status"),
                "home_goals": score.get("home"),
                "away_goals": score.get("away"),
            }
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": f"{competition} {home_team} vs {away_team}",
                        "summary": f"{home_team} vs {away_team}",
                        "headings": [competition, f"{home_team} vs {away_team}"],
                        "fixtures": [fixture],
                        "fixture_candidates": [],
                    },
                )
            )
        return documents
