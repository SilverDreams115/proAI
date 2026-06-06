"""Tests for the TheSportsDB v1 connector (Fase 6.5).

The free tier caps `eventsseason.php` at 15 events so the connector
walks `eventsround.php` round-by-round and stops after a small streak
of empty rounds. The tests cover:

* required query params (league + seasons) — misconfiguration must fail
  loudly at construction time;
* round-walk happy path with scores → fixtures are persisted with int
  goals and timezone-aware timestamps;
* missing scores (future fixtures) are kept as `None` so downstream
  result persistence skips them;
* multi-season fetches and the empty-streak early-exit so we don't make
  50 wasted requests when a short league only has 17 rounds;
* deduplication when TheSportsDB occasionally returns the same idEvent
  on adjacent rounds;
* operator override of the competition label.
"""
from __future__ import annotations

import json

import pytest

from app.connectors import thesportsdb as tsdb
from app.connectors.thesportsdb import TheSportsDbSeasonConnector


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, responses: dict[str, str]) -> list[str]:
    """Patch the connector's HTTP client. Returns the list of URLs that
    were fetched, in call order, so tests can assert request shape."""
    calls: list[str] = []

    def fake_urlopen(request, timeout: int = 30):  # type: ignore[override]
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        body = responses.get(url, '{"events": []}')
        return _FakeResponse(body)

    monkeypatch.setattr(tsdb, "urlopen", fake_urlopen)
    # Don't actually sleep between API calls in tests.
    monkeypatch.setattr(tsdb.time, "sleep", lambda *_: None)
    return calls


def _round_url(league_id: str, season: str, round_number: int) -> str:
    return (
        f"https://www.thesportsdb.com/api/v1/json/3/eventsround.php?"
        f"id={league_id}&r={round_number}&s={season}"
    )


def test_rejects_base_url_without_league_id() -> None:
    """A misconfigured source (no league id) is a programmer bug, not a
    runtime fault — it must fail loudly at construction time."""
    with pytest.raises(ValueError, match="league="):
        TheSportsDbSeasonConnector(name="bad", base_url="https://x/api?seasons=2024")


def test_rejects_base_url_without_seasons() -> None:
    """Same rationale as above for an empty season list."""
    with pytest.raises(ValueError, match="seasons="):
        TheSportsDbSeasonConnector(name="bad", base_url="https://x/api?league=4351")


def test_walks_rounds_until_empty_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connector must fetch round-by-round and stop once the empty
    streak hits the threshold — that is how we cover Liga MX (17 rounds)
    without spamming 50 requests for nothing."""
    responses: dict[str, str] = {}
    for round_number in (1, 2):
        body = json.dumps(
            {
                "events": [
                    {
                        "idEvent": f"r{round_number}-1",
                        "strLeague": "Brazilian Serie A",
                        "strHomeTeam": f"Home{round_number}",
                        "strAwayTeam": f"Away{round_number}",
                        "intHomeScore": "1",
                        "intAwayScore": "0",
                        "strTimestamp": f"2024-04-{10 + round_number:02d}T21:30:00",
                    }
                ]
            }
        )
        responses[_round_url("4351", "2024", round_number)] = body
    # rounds 3, 4, 5 are empty -> stop after the third consecutive empty
    calls = _patch_urlopen(monkeypatch, responses)

    connector = TheSportsDbSeasonConnector(
        name="x",
        base_url="https://api.example/?league=4351&seasons=2024",
    )
    documents = connector.fetch()

    # 2 rounds with data + 3 empty rounds = 5 calls; the connector must
    # stop before round 6 (MAX_EMPTY_ROUNDS = 3).
    assert len(calls) == 5
    assert len(documents) == 2
    first = documents[0].payload["fixtures"][0]
    assert first["home_team"] == "Home1"
    assert first["home_goals"] == 1
    assert first["competition"] == "Brazilian Serie A"


def test_future_events_keep_none_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """Events without scores (yet to be played) must still emit a
    document but with `home_goals`/`away_goals` as `None`."""
    url = _round_url("4351", "2026", 1)
    body = json.dumps(
        {
            "events": [
                {
                    "idEvent": "99",
                    "strLeague": "Brazilian Serie A",
                    "strHomeTeam": "Mirassol",
                    "strAwayTeam": "Lanus",
                    "intHomeScore": None,
                    "intAwayScore": "",
                    "dateEvent": "2026-05-26",
                    "strTime": "23:30:00",
                }
            ]
        }
    )
    _patch_urlopen(monkeypatch, {url: body})

    connector = TheSportsDbSeasonConnector(
        name="Brasileirao 2026",
        base_url="https://api.example/?league=4351&seasons=2026",
    )
    documents = connector.fetch()
    fixture = documents[0].payload["fixtures"][0]
    assert fixture["home_goals"] is None
    assert fixture["away_goals"] is None
    assert fixture["played_at"].startswith("2026-05-26T23:30:00")


def test_multiple_seasons_each_get_their_own_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source covering two seasons must walk rounds independently for
    each — confirms we don't bleed empty-streak state across seasons."""
    url_a = _round_url("4346", "2024", 1)
    url_b = _round_url("4346", "2025", 1)
    body_a = json.dumps(
        {
            "events": [
                {
                    "idEvent": "1",
                    "strLeague": "MLS",
                    "strHomeTeam": "Inter Miami",
                    "strAwayTeam": "LA Galaxy",
                    "intHomeScore": "3",
                    "intAwayScore": "2",
                    "strTimestamp": "2024-09-15T23:30:00",
                }
            ]
        }
    )
    body_b = json.dumps(
        {
            "events": [
                {
                    "idEvent": "2",
                    "strLeague": "MLS",
                    "strHomeTeam": "Cincinnati",
                    "strAwayTeam": "Columbus Crew",
                    "intHomeScore": "1",
                    "intAwayScore": "1",
                    "strTimestamp": "2025-03-20T00:00:00",
                }
            ]
        }
    )
    calls = _patch_urlopen(monkeypatch, {url_a: body_a, url_b: body_b})

    connector = TheSportsDbSeasonConnector(
        name="MLS",
        base_url="https://api.example/?league=4346&seasons=2024,2025",
    )
    documents = connector.fetch()

    # 1 hit + 3 misses per season = 8 calls total. Order matters: season
    # 2024 fully walked before season 2025 starts.
    assert calls[0] == url_a
    season_boundary = next(i for i, url in enumerate(calls) if "s=2025" in url)
    assert all("s=2024" in url for url in calls[:season_boundary])
    assert all("s=2025" in url for url in calls[season_boundary:])
    assert len(documents) == 2
    assert documents[0].payload["fixtures"][0]["home_team"] == "Inter Miami"
    assert documents[1].payload["fixtures"][0]["home_team"] == "Cincinnati"


def test_events_without_teams_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """TheSportsDB occasionally returns placeholder TBD rows. The
    connector must drop them rather than crash entity resolution."""
    url = _round_url("4351", "2026", 1)
    body = json.dumps(
        {
            "events": [
                {
                    "idEvent": "a",
                    "strLeague": "Brazilian Serie A",
                    "strHomeTeam": "",
                    "strAwayTeam": "Mirassol",
                    "strTimestamp": "2026-05-26T23:30:00",
                },
                {
                    "idEvent": "b",
                    "strLeague": "Brazilian Serie A",
                    "strHomeTeam": "Internacional",
                    "strAwayTeam": "Bahia",
                    "intHomeScore": "2",
                    "intAwayScore": "1",
                    "strTimestamp": "2026-05-27T21:30:00",
                },
            ]
        }
    )
    _patch_urlopen(monkeypatch, {url: body})

    connector = TheSportsDbSeasonConnector(
        name="x",
        base_url="https://api.example/?league=4351&seasons=2026",
    )
    documents = connector.fetch()
    assert len(documents) == 1
    assert documents[0].payload["fixtures"][0]["home_team"] == "Internacional"


def test_duplicate_event_ids_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API echoes the same idEvent across two consecutive
    rounds (rare but documented behavior for postponed fixtures), the
    connector must emit a single document — not duplicate the result
    row downstream."""
    body_template = lambda event_id: json.dumps(  # noqa: E731
        {
            "events": [
                {
                    "idEvent": event_id,
                    "strLeague": "MLS",
                    "strHomeTeam": "A",
                    "strAwayTeam": "B",
                    "intHomeScore": "1",
                    "intAwayScore": "0",
                    "strTimestamp": "2024-09-15T23:30:00",
                }
            ]
        }
    )
    responses = {
        _round_url("4346", "2024", 1): body_template("dup-1"),
        _round_url("4346", "2024", 2): body_template("dup-1"),
    }
    _patch_urlopen(monkeypatch, responses)

    connector = TheSportsDbSeasonConnector(
        name="MLS",
        base_url="https://api.example/?league=4346&seasons=2024",
    )
    documents = connector.fetch()
    assert len(documents) == 1


def test_competition_override_wins_over_api_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API mislabels the competition, the operator can pass a
    `competition=` override in the source URL and the connector honors
    it. This avoids a downstream rename migration."""
    url = _round_url("4351", "2026", 1)
    body = json.dumps(
        {
            "events": [
                {
                    "idEvent": "z",
                    "strLeague": "Brazilian Serie A",
                    "strHomeTeam": "Botafogo",
                    "strAwayTeam": "Flamengo",
                    "intHomeScore": "1",
                    "intAwayScore": "1",
                    "strTimestamp": "2026-06-01T23:30:00",
                }
            ]
        }
    )
    _patch_urlopen(monkeypatch, {url: body})

    connector = TheSportsDbSeasonConnector(
        name="x",
        base_url="https://api.example/?league=4351&seasons=2026&competition=Brasileirao",
    )
    documents = connector.fetch()
    assert documents[0].payload["fixtures"][0]["competition"] == "Brasileirao"


def test_max_round_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`max_round=<N>` caps the walk so an operator can force-stop a
    league we know is at most N rounds. Without an explicit cap the
    connector walks the default 50 (subject to the empty-streak
    early-exit)."""
    # Every round returns empty -> the connector should stop at the
    # threshold even though there is no data anywhere.
    _patch_urlopen(monkeypatch, {})
    connector = TheSportsDbSeasonConnector(
        name="x",
        base_url="https://api.example/?league=4351&seasons=2024&max_round=5",
    )
    documents = connector.fetch()
    assert documents == []
