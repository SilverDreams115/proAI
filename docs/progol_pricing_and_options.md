# Progol Pricing & Slate Options (R6.4)

## Pricing — estado: NO verificado

El precio base por boleto **no está validado** contra una fuente oficial en este
entorno. Por política, el sistema **no inventa precios**: mientras el precio sea
`unverified`, el costo estimado se muestra como **"precio no verificado"**
(nunca `$0`, nunca un número inventado).

### Lo que sí es factual (no es precio)
- Progol (weekend) = **14 partidos**.
- Progol Media Semana (midweek) = **9 partidos**.
- Límites de combinaciones (del optimizer existente): weekend ≤8 dobles /
  2 dobles+4 triples; midweek ≤3 dobles / 3 dobles+2 triples.

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

### Matemática
```
combinations  = 2^dobles * 3^triples
estimated_cost = base_price * combinations   (solo si base_price_verified)
               = null                          (si no verificado)
```

### Cómo verificar el precio (manual)
Valida contra una fuente oficial/pública y actualiza la config:
1. TuLotero — https://tulotero.mx/progol/ y https://tulotero.mx/progol-media-semana/
2. Pronósticos para la Asistencia Pública (boleto físico)

Luego en `progol_pricing.py`: set `base_price_mxn`, `base_price_verified=true`,
`source` = origen validado. **No se acepta precio sin fuente.**

### Probe
```bash
python -m scripts.probe_progol_pricing
python -m scripts.probe_progol_pricing --week-type weekend --doubles 8 --triples 0
```

## Slate options — siempre presentes

`GET /api/predictions/slates/{id}/options` · `/active-slates/options` ·
`scripts/audit_slate_options.py`.

Cada slate devuelve **siempre** 4 opciones: Agresiva, Balanceada, Conservadora,
Manual — con combinaciones y costo (o "precio no verificado").

**Respeta Money Mode:**
- `NO_JUGAR` → ninguna opción `recommended`, ninguna `playable`,
  `recommended_action = NO_COMPRAR`; se muestran como "simulaciones no
  recomendadas".
- `JUGAR_*` → la opción correspondiente se marca `recommended`; las otras son
  alternativas.

Estado actual: PG-2338 y PGM-801 → **NO_JUGAR** → opciones visibles pero no
recomendadas, costo "no verificado".
