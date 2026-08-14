"""Progol / Progol MS pricing config + pure cost and legality calculator.

Both the price and the multiple-bet limits are taken from Lotería Nacional's
own "Combinaciones Múltiples y Coperachas" pages (see ``VALIDATION_SOURCES``),
checked 2026-08-14:

* one quiniela sencilla costs **$15 MXN** in both products;
* Progol (14 matches) allows up to **8 dobles** and up to **5 triples**, and
  the published table of allowed combinations tops out at **324 quinielas**
  ($4,860) — 4 triples + 2 dobles, 3 triples + 3 dobles, 2 triples + 5 dobles,
  1 triple + 6 dobles, 5 triples alone, or 8 dobles alone;
* Progol Media Semana (9 matches) allows up to **3 dobles and 2 triples**
  together, i.e. **72 quinielas** ($1,080).

The combination limit is not a house policy: a composition above it cannot be
marked on a real boleto, so a ticket that exceeds it is not expensive, it is
unplayable. ``is_legal_composition`` is the authority and the ticket builder
demotes coverage until it passes.
"""
from __future__ import annotations

from typing import Any

# Price and limits, per product. `base_price_verified` stays a real flag: if a
# future price change is suspected, flip it to False and every estimated cost
# goes back to None rather than showing a stale peso amount.
PROGOL_PRICING: dict[str, dict[str, Any]] = {
    "weekend": {
        "product": "Progol",
        "match_count": 14,
        "base_price_mxn": 15.0,
        "base_price_verified": True,
        # Official ceilings for a single boleto.
        "max_doubles": 8,
        "max_triples": 5,
        # Largest published cell of the combination table ($4,860).
        "max_combinations": 324,
        "source": "loterianacional.gob.mx/Progol/Coperacha (verificado 2026-08-14)",
    },
    "midweek": {
        "product": "Progol Media Semana",
        "match_count": 9,
        "base_price_mxn": 15.0,
        "base_price_verified": True,
        "max_doubles": 3,
        "max_triples": 2,
        # 3 dobles + 2 triples is the corner of the MS table ($1,080).
        "max_combinations": 72,
        "source": "loterianacional.gob.mx/ProgolMediaSemana/Coperacha (verificado 2026-08-14)",
    },
}

# Where the numbers above come from, and where to re-check them.
VALIDATION_SOURCES = [
    "https://www.loterianacional.gob.mx/Progol/Coperacha",
    "https://www.loterianacional.gob.mx/ProgolMediaSemana/Coperacha",
    "https://tulotero.mx/2025/08/19/cuanto-cuesta-una-multiple-en-progol/",
    "Pronósticos para la Asistencia Pública (official) — boleto físico",
]


def _config_for(week_type: str) -> dict[str, Any]:
    return PROGOL_PRICING.get(week_type, PROGOL_PRICING["weekend"])


def combinations(doubles: int, triples: int) -> int:
    """Number of bets a ticket covers: 2^doubles * 3^triples (pure)."""
    return (2 ** max(0, int(doubles))) * (3 ** max(0, int(triples)))


def limits_for(week_type: str) -> dict[str, int]:
    """The three official ceilings a composition has to satisfy at once."""
    config = _config_for(week_type)
    return {
        "max_doubles": int(config["max_doubles"]),
        "max_triples": int(config["max_triples"]),
        "max_combinations": int(config["max_combinations"]),
    }


def legality_for(week_type: str, *, doubles: int, triples: int) -> dict[str, Any]:
    """Check a composition against the published combination table.

    The three ceilings together reproduce Lotería Nacional's table cell for
    cell — for Progol, `max_combinations` is what stops 7 dobles next to a
    triple (384 > 324) while allowing 8 dobles on their own (256).
    """
    limits = limits_for(week_type)
    doubles = max(0, int(doubles))
    triples = max(0, int(triples))
    combos = combinations(doubles, triples)
    violations: list[str] = []
    if doubles > limits["max_doubles"]:
        violations.append(f"doubles {doubles} > {limits['max_doubles']}")
    if triples > limits["max_triples"]:
        violations.append(f"triples {triples} > {limits['max_triples']}")
    if combos > limits["max_combinations"]:
        violations.append(f"combinations {combos} > {limits['max_combinations']}")
    return {
        "legal": not violations,
        "violations": violations,
        "combinations": combos,
        **limits,
    }


def is_legal_composition(week_type: str, *, doubles: int, triples: int) -> bool:
    """True when this many doubles and triples fit on one real boleto."""
    return bool(legality_for(week_type, doubles=doubles, triples=triples)["legal"])


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
    legality = legality_for(week_type, doubles=doubles, triples=triples)
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
        "legal_composition": legality["legal"],
        "legality_violations": legality["violations"],
        "max_combinations": legality["max_combinations"],
    }


def pricing_status() -> dict[str, Any]:
    """Snapshot of the pricing config + verification status (for probe/UI)."""
    return {
        "any_verified": any(c.get("base_price_verified") for c in PROGOL_PRICING.values()),
        "config": PROGOL_PRICING,
        "validation_sources": VALIDATION_SOURCES,
    }
