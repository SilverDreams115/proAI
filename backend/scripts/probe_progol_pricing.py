"""R6.4 — Probe Progol / Progol MS pricing (read-only, no network here).

Reports the current pricing config and whether the base price is verified. It
does NOT scrape a price: validating the official price is a deliberate manual
step against the listed sources (TuLotero / Pronósticos). When the price is
unverified it says so loudly and computes no peso amount.

Usage::

    python -m scripts.probe_progol_pricing
    python -m scripts.probe_progol_pricing --json
    python -m scripts.probe_progol_pricing --doubles 8 --triples 0 --week-type weekend
"""
from __future__ import annotations

import argparse
import json

from app.domain.progol_pricing import compute_cost, pricing_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Progol pricing config (R6.4, read-only).")
    parser.add_argument("--week-type", default=None, help="weekend|midweek (for a cost example)")
    parser.add_argument("--doubles", type=int, default=0)
    parser.add_argument("--triples", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    status = pricing_status()
    example = None
    if args.week_type:
        example = compute_cost(args.week_type, doubles=args.doubles, triples=args.triples)

    payload = {
        "pricing_status": status,
        "example": example,
        "note": (
            "Precio NO verificado: valida manualmente contra TuLotero / Pronósticos "
            "y actualiza progol_pricing.py (base_price_mxn + base_price_verified=true)."
            if not status["any_verified"]
            else "Hay precios verificados en config."
        ),
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0

    print("== Progol pricing ==")
    print(f"  any_verified: {status['any_verified']}")
    for week_type, cfg in status["config"].items():
        price = cfg["base_price_mxn"]
        price_txt = f"${price} MXN" if price is not None else "no verificado"
        print(f"  {week_type:8} {cfg['product']:20} partidos={cfg['match_count']} "
              f"precio={price_txt} verified={cfg['base_price_verified']} "
              f"max_dobles={cfg['max_doubles']} max_triples={cfg['max_triples']} "
              f"source={cfg['source']}")
    print("  validar en:")
    for src in status["validation_sources"]:
        print(f"    - {src}")
    if example:
        cost = example["estimated_cost"]
        cost_txt = f"${cost} MXN" if cost is not None else "requiere validar precio"
        print(f"  ejemplo {example['week_type']} D={args.doubles} T={args.triples}: "
              f"combinaciones={example['combinations']} costo={cost_txt} "
              f"({example['price_status']})")
    print(f"  {payload['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
