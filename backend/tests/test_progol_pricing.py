"""Progol pricing config + pure calculator + the official combination table."""
from __future__ import annotations

import pytest

from app.domain import progol_pricing
from app.domain.progol_pricing import (
    combinations,
    compute_cost,
    is_legal_composition,
    limits_for,
    pricing_status,
)

# Lotería Nacional's published "Combinaciones Múltiples" tables, transcribed
# as {triples: highest number of doubles allowed alongside them}. Checked
# 2026-08-14 against loterianacional.gob.mx. Progol tops out at 324 quinielas
# ($4,860) and Progol Media Semana at 72 ($1,080).
OFFICIAL_TABLE = {
    "weekend": {0: 8, 1: 6, 2: 5, 3: 3, 4: 2, 5: 0},
    "midweek": {0: 3, 1: 3, 2: 3},
}


def test_combinations_is_two_pow_d_three_pow_t():
    """1 — combinations = 2^D * 3^T."""
    assert combinations(0, 0) == 1
    assert combinations(8, 0) == 256
    assert combinations(0, 4) == 81
    assert combinations(2, 4) == 4 * 81  # 324
    assert combinations(3, 2) == 8 * 9  # 72


@pytest.mark.parametrize("week_type", ["weekend", "midweek"])
def test_legality_reproduces_the_official_table(week_type):
    """Every cell of the published table is legal and everything else is not.

    This is the whole point of keeping three ceilings instead of one: for
    Progol, `max_combinations` is what allows 8 dobles on their own (256) and
    still refuses 7 dobles next to a single triple (384), while `max_doubles`
    is what refuses 4 dobles in Media Semana even though 16 would fit under
    the 72-quiniela ceiling.
    """
    allowed = OFFICIAL_TABLE[week_type]
    for triples in range(0, 7):
        for doubles in range(0, 10):
            expected = triples in allowed and doubles <= allowed[triples]
            actual = is_legal_composition(week_type, doubles=doubles, triples=triples)
            assert actual is expected, (
                f"{week_type}: {doubles} dobles + {triples} triples "
                f"({combinations(doubles, triples)} quinielas) — "
                f"expected legal={expected}"
            )


@pytest.mark.parametrize(
    "week_type,doubles,triples,expected_cost",
    [
        # Cost cells taken straight off the official tables.
        ("weekend", 0, 0, 15.0),
        ("weekend", 8, 0, 3840.0),
        ("weekend", 2, 4, 4860.0),
        ("weekend", 3, 3, 3240.0),
        ("weekend", 0, 5, 3645.0),
        ("midweek", 3, 2, 1080.0),
        ("midweek", 0, 2, 135.0),
        ("midweek", 3, 0, 120.0),
    ],
)
def test_cost_matches_the_published_cells(week_type, doubles, triples, expected_cost):
    cost = compute_cost(week_type, doubles=doubles, triples=triples)
    assert cost["price_status"] == "verified"
    assert cost["estimated_cost"] == pytest.approx(expected_cost)
    assert cost["currency"] == "MXN"


def test_cost_is_null_when_a_price_is_marked_unverified(monkeypatch):
    """Un-verifying a price must blank the cost, never show a stale amount."""
    cfg = dict(progol_pricing.PROGOL_PRICING["weekend"])
    cfg.update({"base_price_mxn": None, "base_price_verified": False, "source": "pending"})
    monkeypatch.setitem(progol_pricing.PROGOL_PRICING, "weekend", cfg)

    cost = compute_cost("weekend", doubles=8, triples=0)
    assert cost["price_status"] == "unverified"
    assert cost["estimated_cost"] is None
    assert cost["combinations"] == 256


def test_every_verified_price_carries_a_source():
    """No price is accepted without saying where it came from."""
    for week_type, cfg in pricing_status()["config"].items():
        if not cfg["base_price_verified"]:
            continue
        assert cfg["base_price_mxn"] is not None, week_type
        assert cfg["source"] and cfg["source"] != "pending_validation", week_type
        assert "verificado" in cfg["source"], week_type


def test_weekend_uses_14_matches():
    """weekend product is Progol with 14 matches."""
    cfg = pricing_status()["config"]["weekend"]
    assert cfg["match_count"] == 14
    assert cfg["product"] == "Progol"
    assert limits_for("weekend") == {
        "max_doubles": 8,
        "max_triples": 5,
        "max_combinations": 324,
    }


def test_midweek_uses_9_matches():
    """midweek product is Progol MS with 9 matches."""
    cfg = pricing_status()["config"]["midweek"]
    assert cfg["match_count"] == 9
    assert "Media Semana" in cfg["product"]
    assert limits_for("midweek") == {
        "max_doubles": 3,
        "max_triples": 2,
        "max_combinations": 72,
    }
