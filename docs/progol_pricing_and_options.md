# Progol Pricing & Slate Options

## Pricing — state: verified 2026-08-14

Base price and combination limits come from Lotería Nacional's own
"Combinaciones Múltiples y Coperachas" pages. Both products cost the same per
bet:

- **$15.00 MXN** per quiniela sencilla — Progol and Progol Media Semana.
- Progol (weekend) = **14 matches**; Progol Media Semana (midweek) = **9**.

Verification is a flag, not a fact of nature: if a price change is suspected,
set `base_price_verified=false` in `backend/app/domain/progol_pricing.py` and
every estimated cost goes back to `null` ("precio no verificado") rather than
showing a stale amount. **No price is accepted without a source.**

## How many doubles and triples fit on one boleto

This is a hard external limit, not a house budget: a composition above the
published table cannot be marked on a real ticket, so exceeding it does not
make a ticket expensive — it makes it unplayable.

### Progol (14 matches) — max 8 dobles, max 5 triples, **max 324 quinielas**

| triples | max dobles | quinielas | costo |
|---|---|---|---|
| 0 | 8 | 256 | $3,840 |
| 1 | 6 | 192 | $2,880 |
| 2 | 5 | 288 | $4,320 |
| 3 | 3 | 216 | $3,240 |
| 4 | 2 | 324 | $4,860 |
| 5 | 0 | 243 | $3,645 |

### Progol Media Semana (9 matches) — max 3 dobles, max 2 triples, **max 72**

| triples | max dobles | quinielas | costo |
|---|---|---|---|
| 0 | 3 | 8 | $120 |
| 1 | 3 | 24 | $360 |
| 2 | 3 | 72 | $1,080 |

Those tables are reproduced cell for cell by three ceilings — `max_doubles`,
`max_triples` and `max_combinations` — and `test_progol_pricing.py` asserts the
whole grid. All three are needed: for Progol, `max_combinations` is what allows
8 dobles alone (256) while refusing 7 dobles next to one triple (384); for
Media Semana, `max_doubles` is what refuses 4 dobles even though 16 quinielas
would fit under the 72 ceiling.

### Config
`backend/app/domain/progol_pricing.py`:
```json
{ "weekend": { "product": "Progol", "match_count": 14, "base_price_mxn": 15.0,
  "base_price_verified": true, "max_doubles": 8, "max_triples": 5,
  "max_combinations": 324, "source": "loterianacional.gob.mx/Progol/Coperacha" },
  "midweek": { "product": "Progol Media Semana", "match_count": 9,
  "base_price_mxn": 15.0, "base_price_verified": true, "max_doubles": 3,
  "max_triples": 2, "max_combinations": 72,
  "source": "loterianacional.gob.mx/ProgolMediaSemana/Coperacha" } }
```

### Math
```
combinations   = 2^doubles * 3^triples
legal          = doubles <= max_doubles
                 and triples <= max_triples
                 and combinations <= max_combinations
estimated_cost = base_price * combinations   (only if base_price_verified)
               = null                          (if not verified)
```

### Sources
1. https://www.loterianacional.gob.mx/Progol/Coperacha
2. https://www.loterianacional.gob.mx/ProgolMediaSemana/Coperacha
3. https://tulotero.mx/2025/08/19/cuanto-cuesta-una-multiple-en-progol/
4. Pronósticos para la Asistencia Pública (official) — boleto físico

### Probe
```bash
python -m scripts.probe_progol_pricing
python -m scripts.probe_progol_pricing --week-type weekend --doubles 8 --triples 0
```

## Legality is enforced on the way out

`TicketRecommendationService._enforce_legal_composition` runs last, after the
optimizer, the nesting lift and the draw-coverage floor. Two legal tickets
merge into an illegal one: `full` inherits every double the doubles-only ticket
covers and keeps its own triples on top. PG-2346's conservative ticket reached
4 triples and 4 doubles — 1,296 quinielas against a ceiling of 324.

What comes off first is coverage the mode's own optimizer never asked for (the
lifts), cheapest outcome first, which normally lands the ticket back on the
plan the optimizer had costed. Nesting gives way when it must: once the
doubles-only ticket spends 8 dobles (256), the rules leave no room for a triple
on top, so a `full` that contained it could only be the same ticket again.

## Slate options — always present

`GET /api/predictions/slates/{id}/options` · `/active-slates/options` ·
`scripts/audit_slate_options.py`.

Each slate **always** returns 4 options: Agresiva, Balanceada, Conservadora,
Manual — with combinations and cost in pesos.

**Respects Money Mode:**
- `NO_JUGAR` → no option `recommended`, none `playable`,
  `recommended_action = NO_COMPRAR`; they are shown as "non-recommended
  simulations".
- `JUGAR_*` → the corresponding option is marked `recommended`; the others are
  alternatives.

Money Mode tickets carry the same numbers: `estimated_cost`, `base_price_mxn`,
`price_status`, `pricing_source`, plus `legal_composition` and
`legality_violations` so an operator never plays a ticket on trust alone.
`scripts/operate_money_mode.py` prints a `COSTO` line per slate with the three
tickets priced and the recommended one starred.
