"""TheSportsDB v1 connector (Fase 6.5).

Fetches league-season events from TheSportsDB's free v1 API (key `3`) and
emits one `SourceDocument` per event, in the `sports_feed_v1` payload
shape so the existing parser pipeline persists the row as a
`MatchResultModel` attached to a `MatchModel`.

The connector encodes its target league + season list in `base_url` so the
existing `SourceModel.base_url` column is sufficient and no extra schema
migration is needed:

    https://www.thesportsdb.com/api/v1/json/3?league=<id>&seasons=<s1>,<s2>&max_round=<N>

The free tier caps `eventsseason.php` at 15 events, so the connector
walks `eventsround.php` from round 1 up to `max_round` (default 50) and
stops after `MAX_EMPTY_ROUNDS` consecutive empty responses — that way a
short league like Liga MX (17 rounds) doesn't pay for 50 round requests
while Brasileirao (38 rounds) still gets every match.

Sequential requests with a small delay stay under TheSportsDB's free-tier
rate limit. Missing scores (future events) yield `None`, which the parser
treats as "no result row yet" — only fixtures with both goals fields
populated become `MatchResultModel` rows downstream.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen


class TheSportsDbSeasonConnector(SourceConnector):
    """One source = one (league_id, list-of-seasons) tuple."""

    API_HOST = "https://www.thesportsdb.com/api/v1/json/3"
    REQUEST_DELAY_SECONDS = 2.0  # free tier rate-limits ~30 req/min
    RATE_LIMIT_BACKOFF_SECONDS = (5.0, 15.0, 45.0)
    DEFAULT_MAX_ROUND = 50
    # Stop iterating rounds after this many empty responses in a row —
    # different leagues have different schedule shapes (Brasileirao has
    # 38 rounds; Liga MX 17 per torneo) so a static upper bound would
    # waste calls or miss data.
    MAX_EMPTY_ROUNDS = 3

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "thesportsdb_season"
        self.base_url = base_url
        self.description = "TheSportsDB v1 league-season events feed."
        parsed = urlsplit(base_url)
        params = parse_qs(parsed.query)
        league_values = params.get("league") or params.get("id") or []
        if not league_values:
            raise ValueError(
                "TheSportsDB base_url must include a `league=<id>` query parameter."
            )
        self._league_id = league_values[0].strip()
        if not self._league_id:
            raise ValueError("TheSportsDB league id cannot be empty.")
        seasons_raw = params.get("seasons") or params.get("season") or []
        if not seasons_raw:
            raise ValueError(
                "TheSportsDB base_url must include a `seasons=<a>,<b>` query parameter."
            )
        self._seasons = [
            season.strip()
            for season in ",".join(seasons_raw).split(",")
            if season.strip()
        ]
        if not self._seasons:
            raise ValueError("TheSportsDB seasons list is empty after parsing.")
        override_values = params.get("competition") or []
        self._competition_override = (
            override_values[0].strip() if override_values else ""
        )
        max_round_values = params.get("max_round") or []
        try:
            self._max_round = (
                int(max_round_values[0]) if max_round_values else self.DEFAULT_MAX_ROUND
            )
        except ValueError:
            self._max_round = self.DEFAULT_MAX_ROUND

    def fetch(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        seen_event_ids: set[str] = set()
        captured = datetime.now(timezone.utc)
        request_index = 0
        for season in self._seasons:
            empty_streak = 0
            for round_number in range(1, self._max_round + 1):
                if request_index > 0:
                    time.sleep(self.REQUEST_DELAY_SECONDS)
                request_index += 1
                url = (
                    f"{self.API_HOST}/eventsround.php?"
                    f"id={self._league_id}&r={round_number}&s={season}"
                )
                body = self._fetch_with_backoff(url)
                payload = json.loads(body) if body.strip() else {"events": []}
                events = payload.get("events") or []
                if not events:
                    empty_streak += 1
                    if empty_streak >= self.MAX_EMPTY_ROUNDS:
                        break
                    continue
                empty_streak = 0
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("idEvent") or "")
                    if event_id and event_id in seen_event_ids:
                        continue
                    if event_id:
                        seen_event_ids.add(event_id)
                    doc = self._to_document(event, url, captured)
                    if doc is not None:
                        documents.append(doc)
        return documents

    def _to_document(
        self,
        event: dict[str, Any],
        source_url: str,
        captured_at: datetime,
    ) -> SourceDocument | None:
        home_team = (event.get("strHomeTeam") or "").strip()
        away_team = (event.get("strAwayTeam") or "").strip()
        if not home_team or not away_team:
            return None
        played_at = self._normalize_timestamp(
            event.get("strTimestamp"),
            event.get("dateEvent"),
            event.get("strTime"),
        )
        if played_at is None:
            return None
        competition = (
            self._competition_override
            or (event.get("strLeague") or "").strip()
            or "Unknown League"
        )
        home_goals = self._parse_score(event.get("intHomeScore"))
        away_goals = self._parse_score(event.get("intAwayScore"))
        fixture = {
            "competition": competition,
            "home_team": home_team,
            "away_team": away_team,
            "played_at": played_at,
            "home_goals": home_goals,
            "away_goals": away_goals,
        }
        return SourceDocument(
            source_name=self.name,
            source_url=source_url,
            captured_at=captured_at,
            payload={
                "title": f"{competition} {home_team} vs {away_team}",
                "summary": f"{home_team} vs {away_team}",
                "headings": [competition, f"{home_team} vs {away_team}"],
                "fixtures": [fixture],
                "fixture_candidates": [],
            },
        )

    def _fetch_with_backoff(self, url: str) -> str:
        """Fetch a TheSportsDB endpoint, retrying with exponential
        backoff on 429s. The free tier rate-limits to ~30 req/min and a
        single 429 in the middle of a backfill would otherwise abort
        the whole season — the backoff lets the connector ride out the
        cooldown instead of failing the run."""
        last_error: HTTPError | None = None
        for attempt, wait_for in enumerate((0.0, *self.RATE_LIMIT_BACKOFF_SECONDS)):
            if wait_for > 0:
                time.sleep(wait_for)
            request = Request(
                url,
                headers={"User-Agent": "proAI/0.1 (+https://local.proai)"},
            )
            try:
                with urlopen(request, timeout=30) as response:
                    return response.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code != 429:
                    raise
                last_error = exc
        # Exhausted all retries — propagate the last 429 so the
        # IngestionService records it as the failure reason.
        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_score(value: Any) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_timestamp(
        timestamp: Any,
        date_event: Any,
        time_event: Any,
    ) -> str | None:
        if timestamp:
            try:
                value = str(timestamp).strip()
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
        if date_event:
            time_part = str(time_event).strip() if time_event else "00:00:00"
            if not time_part:
                time_part = "00:00:00"
            try:
                parsed = datetime.fromisoformat(f"{date_event}T{time_part}")
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                return None
        return None
