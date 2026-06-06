from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from urllib.parse import parse_qs
from urllib.parse import urlsplit
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class FootballDataUkSeasonConnector(SourceConnector):
    """Connector for football-data.co.uk season CSV exports."""

    def __init__(self, name: str, base_url: str, season_path: str) -> None:
        self.name = name
        self.kind = "football_data_uk_csv"
        self.base_url = base_url.rstrip("/")
        self.description = "football-data.co.uk season CSV feed."
        parsed_path = urlsplit(season_path.lstrip("/"))
        self.season_path = parsed_path.path
        self._allowed_seasons = {
            season.strip()
            for season in parse_qs(parsed_path.query).get("season", [])
            if season.strip()
        }
        self._competition_code = self._infer_competition_code(self.season_path)

    def fetch(self) -> list[SourceDocument]:
        url = f"{self.base_url}/{self.season_path}"
        request = Request(url, headers={"User-Agent": "proAI/0.1"})
        with urlopen(request, timeout=20) as response:
            content = response.read().decode("utf-8", errors="replace")

        reader = csv.DictReader(StringIO(content))
        documents: list[SourceDocument] = []
        for row in reader:
            season_name = (row.get("Season") or "").strip()
            if self._allowed_seasons and season_name not in self._allowed_seasons:
                continue
            home_team = (row.get("HomeTeam") or row.get("Home") or "").strip()
            away_team = (row.get("AwayTeam") or row.get("Away") or "").strip()
            league = (
                (row.get("League") or row.get("Div") or "").strip()
                or self._competition_code
                or "Unknown League"
            )
            date_value = row.get("Date") or ""
            played_at = self._normalize_played_at(date_value)
            home_goals = row.get("FTHG") or row.get("HG")
            away_goals = row.get("FTAG") or row.get("AG")
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": f"{league} {home_team} vs {away_team}",
                        "summary": f"{home_team} vs {away_team}",
                        "headings": [league, f"{home_team} vs {away_team}"],
                        "fixtures": [
                            {
                                "competition": league,
                                "home_team": home_team,
                                "away_team": away_team,
                                "played_at": played_at,
                                "home_goals": home_goals,
                                "away_goals": away_goals,
                            }
                        ],
                        "fixture_candidates": [],
                    },
                )
            )
        return documents

    def _normalize_played_at(self, raw_value: str) -> str:
        value = raw_value.strip()
        if not value:
            return datetime.now(timezone.utc).isoformat()
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()

    def _infer_competition_code(self, season_path: str) -> str:
        filename = season_path.rsplit("/", 1)[-1]
        stem = filename.split(".", 1)[0].strip().upper()
        return stem or "Unknown League"
