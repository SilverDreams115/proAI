"""R6.4 — Progol / Progol MS pricing config + pure cost calculator.

The pricing here is **deliberately unverified**. The only facts encoded are the
product structure that is public and stable — Progol (weekend) has 14 matches,
Progol Media Semana (midweek) has 9 — plus the optimizer's existing combination
caps. The **base price is NOT known/validated from a live official source in
this environment**, so ``base_price_verified=false`` and ``base_price_mxn=None``.

Consequence (enforced by ``compute_cost``): while the price is unverified,
``estimated_cost`` is ``None`` and surfaces as "precio no verificado" — the
system never shows an invented peso amount and never shows ``$0``.

To verify a price, validate it manually against an official/public source
(``validation_sources`` below) and set ``base_price_mxn`` + flip
``base_price_verified=true`` + ``source`` to the validated origin. No price is
accepted without a source.
"""
from __future__ import annotations

from typing import Any

# Public, stable structure facts (NOT pricing): match counts per product, and
# the optimizer's existing practical caps on doubles/triples (already used by
# TicketRecommendationService.MULTIPLE_RULES). These are limits, not prices.
PROGOL_PRICING: dict[str, dict[str, Any]] = {
    "weekend": {
        "product": "Progol",
        "match_count": 14,
        "base_price_mxn": None,
        "base_price_verified": False,
        # Combination caps mirror TicketRecommendationService.MULTIPLE_RULES.
        "max_doubles": 8,
        "max_triples": 4,
        "source": "pending_validation",
    },
    "midweek": {
        "product": "Progol Media Semana",
        "match_count": 9,
        "base_price_mxn": None,
        "base_price_verified": False,
        "max_doubles": 3,
        "max_triples": 2,
        "source": "pending_validation",
    },
}

# Where an operator should validate the real price (manual, not scraped here).
VALIDATION_SOURCES = [
    "https://tulotero.mx/progol/",
    "https://tulotero.mx/progol-media-semana/",
    "Pronósticos para la Asistencia Pública (official) — boleto físico",
]


def _config_for(week_type: str) -> dict[str, Any]:
    return PROGOL_PRICING.get(week_type, PROGOL_PRICING["weekend"])


def combinations(doubles: int, triples: int) -> int:
    """Number of bets a ticket covers: 2^doubles * 3^triples (pure)."""
    return (2 ** max(0, int(doubles))) * (3 ** max(0, int(triples)))


def compute_cost(week_type: str, *, doubles: int, triples: int) -> dict[str, Any]:
    """Pure pricing projection for a ticket composition.

    ``estimated_cost`` is the base price times the combinations ONLY when the
    base price is verified; otherwise it is ``None`` (never invented, never $0).
    """
    config = _config_for(week_type)
    combos = combinations(doubles, triples)
    base = config.get("base_price_mxn")
    verified = bool(config.get("base_price_verified"))
    estimated_cost = round(float(base) * combos, 2) if (verified and base is not None) else None
    return {
        "product": config["product"],
        "week_type": week_type,
        "combinations": combos,
        "base_price_mxn": base,
        "base_price_verified": verified,
        "price_status": "verified" if verified else "unverified",
        "estimated_cost": estimated_cost,
        "currency": "MXN",
        "source": config.get("source"),
    }


def pricing_status() -> dict[str, Any]:
    """Snapshot of the pricing config + verification status (for probe/UI)."""
    return {
        "any_verified": any(c.get("base_price_verified") for c in PROGOL_PRICING.values()),
        "config": PROGOL_PRICING,
        "validation_sources": VALIDATION_SOURCES,
    }
