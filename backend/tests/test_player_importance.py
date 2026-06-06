"""Tests for the player-importance weighting of context signals (Fase 3.1)."""
from __future__ import annotations

import math
from types import SimpleNamespace

from app.services.feature_service import FeatureService


def _make_match():
    return SimpleNamespace(
        home_team_id="home-team-id",
        away_team_id="away-team-id",
        home_team=SimpleNamespace(name="Local FC"),
        away_team=SimpleNamespace(name="Visitor FC"),
    )


def _availability(*, team_id: str, impact: float, player=None, category: str = "injury"):
    """Minimal duck-type for a PlayerAvailabilityModel row."""
    return SimpleNamespace(
        team_id=team_id,
        impact_score=impact,
        category=category,
        player=player,
    )


def _player(*, role: str | None = None, position: str | None = None, team_id: str = "home-team-id"):
    """Player stub carrying just the fields _player_importance reads."""
    team_links = [SimpleNamespace(team_id=team_id, squad_role=role)] if role else []
    return SimpleNamespace(primary_position=position, team_links=team_links)


def _signals(availability_items):
    return FeatureService(SimpleNamespace())._extract_narrative_signals(
        _make_match(), [], availability_items
    )


def test_anonymous_availability_keeps_flat_impact() -> None:
    """When the row has no `player` attribute, the multiplier is 1.0 — we
    keep the legacy behavior so untagged rows do not regress."""
    item = _availability(team_id="home-team-id", impact=0.85, player=None)
    signals = _signals([item])
    assert math.isclose(signals["home_availability_impact"], 0.85, abs_tol=1e-6)


def test_starter_player_is_weighted_above_anonymous() -> None:
    """A confirmed starter must produce a larger impact than the same row
    without metadata — that is the whole point of F3.1."""
    starter = _player(role="starter", team_id="home-team-id")
    bench = _player(role="reserve", team_id="home-team-id")
    signals_starter = _signals([_availability(team_id="home-team-id", impact=0.5, player=starter)])
    signals_bench = _signals([_availability(team_id="home-team-id", impact=0.5, player=bench)])
    assert signals_starter["home_availability_impact"] > 0.5
    assert signals_bench["home_availability_impact"] < 0.5
    assert signals_starter["home_availability_impact"] > signals_bench["home_availability_impact"]


def test_goalkeeper_position_amplifies_impact_more_than_field_player() -> None:
    """Two starters with the same impact_score; the GK's absence outweighs
    a generic midfielder's because position carries an extra multiplier."""
    goalkeeper = _player(role="starter", position="goalkeeper", team_id="home-team-id")
    midfielder = _player(role="starter", position="midfielder", team_id="home-team-id")
    gk_signal = _signals([_availability(team_id="home-team-id", impact=0.5, player=goalkeeper)])
    mid_signal = _signals([_availability(team_id="home-team-id", impact=0.5, player=midfielder)])
    assert gk_signal["home_availability_impact"] > mid_signal["home_availability_impact"]


def test_player_importance_is_capped() -> None:
    """Even a starter + striker combination must not exceed 2x the impact —
    we never want a single role label to dominate the signal."""
    striker_captain = _player(role="captain starter", position="striker", team_id="home-team-id")
    signal = _signals([_availability(team_id="home-team-id", impact=1.0, player=striker_captain)])
    assert signal["home_availability_impact"] <= 2.0 + 1e-6


def test_home_and_away_weights_are_routed_correctly() -> None:
    """A starter missing on the AWAY side must not bleed into the home
    impact signal — the team_id is what drives the routing."""
    away_starter = _player(role="starter", team_id="away-team-id")
    signals = _signals([_availability(team_id="away-team-id", impact=0.6, player=away_starter)])
    assert signals["home_availability_impact"] == 0.0
    assert signals["away_availability_impact"] > 0.6
