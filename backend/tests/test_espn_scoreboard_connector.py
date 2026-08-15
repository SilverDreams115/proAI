"""ESPN scoreboard connector — the keyless feed for the competitions no other
source covers (CONMEBOL Sudamericana, UEFA qualifying).

The connector's job is to turn ESPN events into the same fixture shape
`sports_feed_v1` already reads from football-data.org, and to survive an
undocumented endpoint changing under it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.connectors.espn_scoreboard import (
    EspnScoreboardConnector,
    _fixture_from_event,
    parse_leagues,
)

BASE = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer"
    "?leagues=conmebol.sudamericana,conmebol.libertadores&days_back=3&days_ahead=10"
)


def _event(
    *,
    home="Club Olimpia",
    away="Vasco da Gama",
    status="STATUS_SCHEDULED",
    home_score=None,
    away_score=None,
    date="2026-08-20T22:00Z",
):
    return {
        "date": date,
        "competitions": [
            {
                "status": {"type": {"name": status}},
                "venue": {"fullName": "Estadio Defensores del Chaco"},
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
                    {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
                ],
            }
        ],
    }


def test_parse_leagues_keeps_order_and_drops_duplicates():
    assert parse_leagues(BASE) == ["conmebol.sudamericana", "conmebol.libertadores"]
    assert parse_leagues("https://x/y?leagues=a,,b, a ") == ["a", "b"]
    assert parse_leagues("https://x/y") == []


def test_scheduled_event_becomes_a_fixture_with_no_goals():
    fixture = _fixture_from_event(_event(), "CONMEBOL Sudamericana", "conmebol.sudamericana")
    assert fixture is not None
    assert fixture["home_team"] == "Club Olimpia"
    assert fixture["away_team"] == "Vasco da Gama"
    assert fixture["competition"] == "CONMEBOL Sudamericana"
    assert fixture["kickoff_at"] == "2026-08-20T22:00Z"
    assert fixture["status"] == "SCHEDULED"
    assert fixture["home_goals"] is None and fixture["away_goals"] is None


def test_finished_event_carries_the_score():
    fixture = _fixture_from_event(
        _event(status="STATUS_FULL_TIME", home_score="2", away_score="1"),
        "CONMEBOL Sudamericana",
        "conmebol.sudamericana",
    )
    assert fixture is not None
    assert fixture["status"] == "FINISHED"
    assert fixture["home_goals"] == 2
    assert fixture["away_goals"] == 1


def test_in_play_score_is_not_ingested_as_a_result():
    """A live 1-0 is not a result. Reading it as one would score a prediction
    against a match still being played."""
    fixture = _fixture_from_event(
        _event(status="STATUS_IN_PROGRESS", home_score="1", away_score="0"),
        "MLS",
        "usa.1",
    )
    assert fixture is not None
    assert fixture["status"] == "IN_PLAY"
    assert fixture["home_goals"] is None
    assert fixture["away_goals"] is None


@pytest.mark.parametrize(
    "event",
    [
        {"competitions": []},
        {"competitions": [{"competitors": []}]},
        # One-sided event (ESPN does this for some placeholder rows).
        {"competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "X"}}]}]},
        # Both sides present but a name is blank.
        {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": ""}},
                        {"homeAway": "away", "team": {"displayName": "Y"}},
                    ]
                }
            ]
        },
    ],
)
def test_malformed_events_are_dropped_not_half_built(event):
    """Better no fixture than one with a blank side: a half-built row would
    resolve against the wrong match or create a placeholder team."""
    assert _fixture_from_event(event, "L", "l") is None


def test_one_failing_league_does_not_sink_the_others(monkeypatch):
    """The endpoint is undocumented and a slug can vanish. The run keeps the
    leagues that answered."""
    import app.connectors.espn_scoreboard as mod

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        url = request.full_url
        if "sudamericana" in url:
            raise OSError("HTTP Error 404: Not Found")
        body = json.dumps(
            {
                "leagues": [{"name": "CONMEBOL Libertadores"}],
                "events": [_event(home="Corinthians", away="Rosario Central")],
            }
        ).encode()
        return _Response(body)

    monkeypatch.setattr(mod, "urlopen", _fake_urlopen)
    documents = EspnScoreboardConnector("ESPN", BASE).fetch()

    assert len(documents) == 1
    fixture = documents[0].payload["fixtures"][0]
    assert fixture["home_team"] == "Corinthians"
    assert fixture["competition"] == "CONMEBOL Libertadores"


def test_window_covers_played_and_upcoming(monkeypatch):
    """days_back feeds the learning loop, days_ahead feeds the resolver."""
    import app.connectors.espn_scoreboard as mod

    seen: list[str] = []

    class _Response:
        def read(self) -> bytes:
            return b'{"leagues": [{"name": "L"}], "events": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _fake_urlopen(request, timeout=0):  # noqa: ARG001
        seen.append(request.full_url)
        return _Response()

    monkeypatch.setattr(mod, "urlopen", _fake_urlopen)
    EspnScoreboardConnector("ESPN", "https://site.api.espn.com/x?leagues=a&days_back=2&days_ahead=5").fetch()

    today = datetime.now(timezone.utc).date()
    expected = (
        f"{(today - timedelta(days=2)):%Y%m%d}-{(today + timedelta(days=5)):%Y%m%d}"
    )
    assert seen and expected in seen[0]


def test_no_leagues_configured_fetches_nothing(monkeypatch):
    import app.connectors.espn_scoreboard as mod

    def _boom(*_a, **_k):
        raise AssertionError("must not call the network without a league list")

    monkeypatch.setattr(mod, "urlopen", _boom)
    assert EspnScoreboardConnector("ESPN", "https://site.api.espn.com/x").fetch() == []
