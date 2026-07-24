# Progol Pricing & Slate Options (R6.4)

## Pricing — state: NOT verified

The base price per ticket **is not validated** against an official source in this
environment. By policy, the system **does not invent prices**: while the price is
`unverified`, the estimated cost is shown as **"precio no verificado"**
(never `$0`, never an invented number).

### What is factual (not a price)
- Progol (weekend) = **14 matches**.
- Progol Media Semana (midweek) = **9 matches**.
- Combination limits (from the existing optimizer): weekend ≤8 doubles /
  2 doubles+4 triples; midweek ≤3 doubles / 3 doubles+2 triples.

### Config
`backend/app/domain/progol_pricing.py`:
```json
{ "weekend": { "product": "Progol", "match_count": 14, "base_price_mxn": null,
  "base_price_verified": false, "max_doubles": 8, "max_triples": 4,
  "source": "pending_validation" },
  "midweek": { "product": "Progol Media Semana", "match_count": 9,
  "base_price_mxn": null, "base_price_verified": false, "max_doubles": 3,
  "max_triples": 2, "source": "pending_validation" } }
```

### Math
```
combinations  = 2^doubles * 3^triples
estimated_cost = base_price * combinations   (only if base_price_verified)
               = null                          (if not verified)
```

### How to verify the price (manual)
Validate against an official/public source and update the config:
1. TuLotero — https://tulotero.mx/progol/ and https://tulotero.mx/progol-media-semana/
2. Pronósticos para la Asistencia Pública (physical ticket)

Then in `progol_pricing.py`: set `base_price_mxn`, `base_price_verified=true`,
`source` = validated origin. **No price is accepted without a source.**

### Probe
```bash
python -m scripts.probe_progol_pricing
python -m scripts.probe_progol_pricing --week-type weekend --doubles 8 --triples 0
```

## Slate options — always present

`GET /api/predictions/slates/{id}/options` · `/active-slates/options` ·
`scripts/audit_slate_options.py`.

Each slate **always** returns 4 options: Agresiva, Balanceada, Conservadora,
Manual — with combinations and cost (or "precio no verificado").

**Respects Money Mode:**
- `NO_JUGAR` → no option `recommended`, none `playable`,
  `recommended_action = NO_COMPRAR`; they are shown as "non-recommended
  simulations".
- `JUGAR_*` → the corresponding option is marked `recommended`; the others are
  alternatives.

Current state: PG-2338 and PGM-801 → **NO_JUGAR** → options visible but not
recommended, cost "not verified".
