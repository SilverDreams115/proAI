"""R6.4 — Progol pricing config + pure calculator."""
from __future__ import annotations

import pytest

from app.domain import progol_pricing
from app.domain.progol_pricing import combinations, compute_cost, pricing_status


def test_combinations_is_two_pow_d_three_pow_t():
    """1 — combinations = 2^D * 3^T."""
    assert combinations(0, 0) == 1
    assert combinations(8, 0) == 256
    assert combinations(0, 4) == 81
    assert combinations(2, 4) == 4 * 81  # 324
    assert combinations(3, 2) == 8 * 9  # 72


def test_cost_is_null_when_price_unverified():
    """2 — unverified price -> estimated_cost is None (never invented, never $0)."""
    cost = compute_cost("weekend", doubles=8, triples=0)
    assert cost["base_price_verified"] is False
    assert cost["price_status"] == "unverified"
    assert cost["estimated_cost"] is None
    assert cost["combinations"] == 256


def test_cost_is_computed_when_price_verified(monkeypatch):
    """3 — verified price in config -> cost = base_price * combinations."""
    cfg = dict(progol_pricing.PROGOL_PRICING["weekend"])
    cfg.update({"base_price_mxn": 15.0, "base_price_verified": True, "source": "test_verified"})
    monkeypatch.setitem(progol_pricing.PROGOL_PRICING, "weekend", cfg)

    cost = compute_cost("weekend", doubles=2, triples=0)
    assert cost["price_status"] == "verified"
    assert cost["estimated_cost"] == pytest.approx(15.0 * 4)


def test_weekend_uses_14_matches():
    """4 — weekend product is Progol with 14 matches."""
    cfg = pricing_status()["config"]["weekend"]
    assert cfg["match_count"] == 14
    assert cfg["product"] == "Progol"


def test_midweek_uses_9_matches():
    """5 — midweek product is Progol MS with 9 matches."""
    cfg = pricing_status()["config"]["midweek"]
    assert cfg["match_count"] == 9
    assert "Media Semana" in cfg["product"]


def test_no_price_without_source():
    """No verified price ships in config (no invented amount)."""
    for cfg in pricing_status()["config"].values():
        assert cfg["base_price_mxn"] is None
        assert cfg["base_price_verified"] is False
        assert cfg["source"] == "pending_validation"
