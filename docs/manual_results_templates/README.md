# Manual official-results templates (R7.1)

These are **pre-filled, incomplete** templates for loading official Progol/Media
Semana results into the learning loop when no automated source is available.

Each entry already carries the real fixture in `source_note` (read from the
slate's matches). An operator only fills:

- `sign`: `L` (local/home win), `E` (empate/draw) or `V` (visitante/away win)
- `score`: home-away, e.g. `"2-0"` (must agree with `sign`)

The source must be official/verifiable (Pronósticos / TuLotero closed results).
**Never** use predictions, calendars or partial livescores.

## Regenerate a template

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.make_manual_results_template --draw-code PG-2337
```

## Validate (read-only dry-run) — refuses while incomplete

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.validate_completed_slate_results \
  --manual-file /path/to/pg_2337_results_template.json --dry-run
```

A complete file reports `ready_to_apply: true` only when coverage is 100%,
there are no conflicts with existing results, and the source is high-confidence.

## Apply (guarded) — writes to `match_results` only with the exact token

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.validate_completed_slate_results \
  --manual-file /path/to/pg_2337_results_filled.json \
  --apply --confirm APPLY-COMPLETED-SLATE-RESULTS
```

After applying, re-run: `learning_inventory` → `score_completed_slate` →
`audit_learning_calibration` → `audit_learning_dataset_readiness`.

## Reglas aprendidas (backfill 2026-07-16, PG-2336/2339 + MS 799/801/802/803)

- La **cadena oficial L/E/V** de Lotería Nacional es la fuente de verdad del
  signo; los scores de proveedores/prensa solo la complementan. La cadena
  derivada de los scores debe coincidir 100% con la oficial antes de aplicar.
- **Eliminatorias**: Progol cuenta el tiempo regular (90'). Con prórroga o
  penales, capturar el score de 90' (Bélgica 3-2 Senegal aet ⇒ E / 2-2).
- Las **fechas/kickoffs de la DB pueden estar mal** en slates creadas desde la
  guía (placeholders de bracket): no confiar en la ventana de fechas del
  proveedor para identificar el fixture; confiar en el programa oficial del
  concurso.
- Posición con **slot de bracket** ("Ganador X"): relinkear con
  `scripts/relink_slate_team.py` antes de aplicar.
- Pendientes conocidos: PG-2341 (programa con mapeos dudosos y predicciones
  incompletas; cadena oficial 12/07: EELVVLELVLVLLE) y PGM-804 (en curso al
  2026-07-16).

## Fuentes gratuitas de marcadores (verificado 2026-07-24, PGM-805)

LN y TuLotero publican el **signo**, nunca el marcador, y `match_results`
exige `home_goals`/`away_goals` NOT NULL — así que una jornada no se puede
aplicar solo con la cadena oficial. Estas dos fuentes cubren el marcador sin
pagar ni registrar nada:

**football-data.org** (clave ya en `.env`, plan gratuito, 13 competencias):
`BSA` Brasileirão · `PL` · `ELC` · `CL` · `EC` · `FL1` · `BL1` · `SA` · `DED` ·
`PPL` · `CLI` Copa Libertadores · `PD` · `WC`. Ojo: tiene Libertadores pero
**no** Sudamericana, ni Liga MX, ni Argentina, ni MLS.

```bash
curl -H "X-Auth-Token: $PROAI_FOOTBALL_DATA_API_KEY" \
  "https://api.football-data.org/v4/competitions/BSA/matches?dateFrom=2026-07-22&dateTo=2026-07-24"
```

**TheSportsDB** con la clave pública `3`, que **cubre Liga MX, MLS, Primera
Argentina y Copa Sudamericana**. La clave capa toda respuesta a **5 registros**,
así que `eventsseason.php` es inútil; hay que consultar por partido o filtrando
por liga, donde el cap no llega:

```bash
# por partido (el más robusto para ligas raras)
.../json/3/searchevents.php?e=Bolivar_vs_Gremio
# por liga y día (l=<league id>, el de sources.base_url)
.../json/3/eventsday.php?d=2026-07-22&l=4350
```

League ids ya registrados en `sources.base_url`: Liga MX `4350`, MLS `4346`,
Brasileirão `4351`, Primera Argentina `4406`, Chile `4627`.

**Siempre cruzar contra la cadena de LN antes de aplicar.** En PGM-805 los 9
marcadores obtenidos derivaron los 9 signos que LN ya había publicado por
separado — ese acuerdo es la verificación. Si un marcador discrepa del signo
oficial, se descarta el marcador, no el signo.

Límite conocido de `searchevents.php`: falla con variantes de nombre en ligas
oscuras (Segunda uruguaya, Serie B brasileña, noruega) y no resuelve slots de
bracket. En PG-2342 solo resolvió 3 de 14.

`backend/app/connectors/api_football.py` + `scripts/audit_sports_scores.py`
cubrirían el resto (incluye el cruce contra LN), pero necesitan una clave
gratuita de api-sports.io que hay que registrar.
