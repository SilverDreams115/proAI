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
