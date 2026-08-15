"""ESPN public scoreboard connector — fixtures and results, no API key.

The competitions Progol Media Semana is mostly made of are the ones no feed
we had could serve. PGM-809 is the case: nine fixtures, none of them
resolvable. Copa Sudamericana is in neither football-data.org's free tier nor
TheSportsDB's (whose free catalogue now returns ten leagues in total), and
UEFA's qualifying rounds are in neither. So every one of those nine positions
was promoted against a fabricated kickoff — all on the same invented day, an
hour apart — and a competition guessed from team history.

ESPN's site API serves all of them: CONMEBOL Sudamericana and Libertadores,
UEFA qualifying, MLS, and the domestic leagues besides. It needs no key and no
account, and it carries full-time scores as well as scheduled kickoffs, so it
answers both the fixture question and the result question.

It is, however, **undocumented**: this is the endpoint behind espn.com, not a
published product, and there is no contract that it will keep working. The
connector is therefore written to fail small — one league failing is logged
and skipped rather than sinking the run — and everything downstream keeps
treating an unresolved fixture as a placeholder, exactly as it did before this
existed. If ESPN closes the endpoint, the system returns to the behaviour it
has today; it does not break.

Source `base_url` shape (query drives the connector, no per-league subclass)::

    https://site.api.espn.com/apis/site/v2/sports/soccer
        ?leagues=conmebol.sudamericana,conmebol.libertadores,uefa.champions_qual
        &days_back=3&days_ahead=10

`days_back` picks up results the learning loop needs; `days_ahead` picks up
fixtures the resolver needs before a slate closes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument
from app.connectors.http import safe_urlopen as urlopen

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://site.api.espn.com"
DEFAULT_PATH = "/apis/site/v2/sports/soccer"
# Windows chosen for what the two consumers need: results for the jornada just
# played, fixtures for the slate about to close.
DEFAULT_DAYS_BACK = 3
DEFAULT_DAYS_AHEAD = 10

# ESPN status names → the vocabulary `sports_feed_v1` already understands from
# football-data.org. Anything unmapped rides through as-is.
_STATUS_BY_ESPN = {
    "STATUS_SCHEDULED": "SCHEDULED",
    "STATUS_IN_PROGRESS": "IN_PLAY",
    "STATUS_HALFTIME": "IN_PLAY",
    "STATUS_FULL_TIME": "FINISHED",
    "STATUS_FINAL": "FINISHED",
    "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELLED",
    "STATUS_ABANDONED": "CANCELLED",
}


def parse_leagues(base_url: str) -> list[str]:
    """League slugs from the source's query string, in order, de-duplicated."""
    params = parse_qs(urlsplit(base_url).query)
    raw = ",".join(params.get("leagues") or [])
    seen: list[str] = []
    for slug in (part.strip() for part in raw.split(",")):
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def _window(base_url: str) -> tuple[str, str]:
    params = parse_qs(urlsplit(base_url).query)

    def _int(key: str, default: int) -> int:
        values = params.get(key) or []
        if not values:
            return default
        try:
            return max(0, int(values[0]))
        except (TypeError, ValueError):
            return default

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=_int("days_back", DEFAULT_DAYS_BACK))
    end = today + timedelta(days=_int("days_ahead", DEFAULT_DAYS_AHEAD))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _fixture_from_event(event: dict, league_name: str, league_slug: str) -> dict | None:
    """One ESPN event → the fixture shape `sports_feed_v1` reads.

    Returns None when the event is not a two-sided match with both names, so a
    malformed row is dropped instead of creating a fixture with a blank side.
    """
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    competitors = competition.get("competitors") or []
    if len(competitors) != 2:
        return None

    by_side = {str(c.get("homeAway")): c for c in competitors}
    home, away = by_side.get("home"), by_side.get("away")
    if home is None or away is None:
        return None

    def _name(side: dict) -> str:
        team = side.get("team") or {}
        return str(team.get("displayName") or team.get("name") or "").strip()

    def _goals(side: dict) -> int | None:
        score = side.get("score")
        if score is None or score == "":
            return None
        try:
            return int(str(score))
        except (TypeError, ValueError):
            return None

    home_team, away_team = _name(home), _name(away)
    if not home_team or not away_team:
        return None

    espn_status = str(((competition.get("status") or {}).get("type") or {}).get("name") or "")
    status = _STATUS_BY_ESPN.get(espn_status, espn_status or "SCHEDULED")
    kickoff_at = event.get("date")
    venue = ((competition.get("venue") or {}).get("fullName")) or None

    # Goals only once the match is over: an in-play score would otherwise be
    # ingested as a final result.
    finished = status == "FINISHED"
    return {
        "competition": league_name,
        "competition_code": league_slug,
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_at": kickoff_at,
        "played_at": kickoff_at,
        "venue": venue,
        "status": status,
        "home_goals": _goals(home) if finished else None,
        "away_goals": _goals(away) if finished else None,
    }


class EspnScoreboardConnector(SourceConnector):
    """Fetches one or more ESPN soccer league scoreboards over a date window."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.kind = "espn_scoreboard"
        self.base_url = base_url
        self.description = "ESPN public soccer scoreboard (fixtures + results, keyless)."

    def _endpoint(self, league_slug: str, dates: str) -> str:
        parsed = urlsplit(self.base_url)
        host = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else DEFAULT_HOST
        path = parsed.path.rstrip("/") or DEFAULT_PATH
        return f"{host}{path}/{league_slug}/scoreboard?dates={dates}"

    def fetch(self) -> list[SourceDocument]:
        leagues = parse_leagues(self.base_url)
        if not leagues:
            return []
        start, end = _window(self.base_url)
        dates = f"{start}-{end}"

        documents: list[SourceDocument] = []
        for league_slug in leagues:
            url = self._endpoint(league_slug, dates)
            try:
                # No custom User-Agent on purpose. ESPN's edge answers
                # urllib's default and returns 403 to a named client — both
                # "proAI/0.1" and a browser-shaped string were refused on
                # 2026-08-14. Adding one back to "identify ourselves politely"
                # silently kills the feed.
                request = Request(url)
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001 — one league must not sink the run
                # Undocumented endpoint: a slug can disappear without notice.
                # Log it and keep the other leagues.
                logger.warning(
                    "espn scoreboard league failed",
                    extra={
                        "event": "espn_scoreboard_league_failed",
                        "league": league_slug,
                        "url": url,
                        "error": str(exc),
                    },
                )
                continue

            league_meta = (payload.get("leagues") or [{}])[0]
            league_name = str(league_meta.get("name") or league_slug)
            for event in payload.get("events") or []:
                fixture = _fixture_from_event(event, league_name, league_slug)
                if fixture is None:
                    continue
                label = f"{fixture['home_team']} vs {fixture['away_team']}"
                documents.append(
                    SourceDocument(
                        source_name=self.name,
                        source_url=url,
                        captured_at=datetime.now(timezone.utc),
                        payload={
                            "title": f"{league_name} {label}",
                            "summary": label,
                            "headings": [league_name, label],
                            "fixtures": [fixture],
                            "fixture_candidates": [],
                        },
                    )
                )
        return documents
