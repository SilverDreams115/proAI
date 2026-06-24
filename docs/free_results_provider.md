# Free Results Provider (R6.3)

Read-only integration with a **free** results source so the operator can see
external scores without paying for a live feed and without ever writing the
productive `match_results` table.

## Proveedor elegido: football-data.org

- API v4 estructurada, plan gratuito, consulta de `matches` por fecha/competencia/estado.
- **Limitaciones del plan gratuito:**
  - **Scores delayed**, no live real.
  - Requiere **API key** (`X-Auth-Token`).
  - **~10 llamadas/min** (rate limit del free tier).
  - Cobertura de competencias **limitada** (el plan gratis no cubre todo; p. ej.
    amistosos internacionales / mundial pueden no estar).

### Backup / cross-check
- **TheSportsDB** — respaldo/contraste. No se usa como primario porque los
  livescores que necesitamos están en su plan premium. El probe lo reporta como
  `cross_check_only`.
- **Reuters World Cup page** — solo contraste humano/manual, nunca scraper
  principal.

## Configuración (env)

```bash
PROAI_RESULTS_PROVIDER_ENABLED=false          # default: deshabilitado
PROAI_RESULTS_PROVIDER_PRIMARY=football_data_org
PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=true       # default: solo dry-run
PROAI_FOOTBALL_DATA_API_KEY=                    # tu key (NO se hardcodea)
PROAI_FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
```

Defaults seguros: **deshabilitado + dry-run-only + sin escrituras**. Con
`ENABLED=false` no se hace ninguna llamada de red.

## Cómo correr el probe

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.probe_free_results_source --provider football_data_org
# por slate:
... --provider football_data_org --draw-code PG-2338
... --provider football_data_org --active-upcoming --json
```

El probe valida: presencia de API key, accesibilidad del proveedor,
competencias cubiertas, matches/finished encontrados, y cobertura contra la
slate. **Estados posibles:**

| status | significado |
|---|---|
| `ok` | proveedor disponible y con cobertura |
| `disabled` | `PROAI_RESULTS_PROVIDER_ENABLED=false` (no se llama a la red) |
| `unavailable_missing_key` | falta `PROAI_FOOTBALL_DATA_API_KEY` (no fatal) |
| `insufficient_coverage` | el proveedor no cubre esta competencia/slate |
| `provider_error` | fallo de red/proveedor (no fatal) |
| `cross_check_only` | proveedor de respaldo (TheSportsDB / manual) |

## Dry-run por slate (read-only)

```bash
curl -s http://127.0.0.1:8000/api/results/slates/<id>/provider-dry-run
curl -s http://127.0.0.1:8000/api/results/active-slates/provider-dry-run
```

Salida: `provider`, `enabled`, `status`, `coverage {matched,total,rate}` y por
partido `{position, local_match, provider_match, status, score, confidence}`.
Siempre `write_safety.writes_performed = false`.

### Matching de nombres
Reutiliza `NormalizationService`: resuelve aliases y acentos, p. ej.
**México/Mexico**, **E.U.A./USA/Estados Unidos**, **Chequia/Czech Republic**.
`confidence`: `high` (ambos equipos casan), `low` (uno), `none` (sin emparejar).

## Cómo interpretar coverage

- `matched/total` = partidos de la slate que el proveedor emparejó con
  confianza alta. Para las slates actuales (amistosos internacionales) el plan
  gratuito típicamente da **cobertura baja o nula** → `insufficient_coverage`.
  Es el resultado honesto, no un error.

## Cómo NO aplicar resultados automáticamente

- **Nunca** hay apply automático. El dry-run y la UI son solo lectura.
- El apply manual está **bloqueado por diseño** en esta fase:

```bash
python -m scripts.apply_provider_results --draw-code PG-2338 \
    --apply --confirm APPLY-PROVIDER-RESULTS-ONLY
```

Aun con el token correcto, exige `PROAI_RESULTS_PROVIDER_ENABLED=true` y
`PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=false`, y en R6.3 responde
`NOT IMPLEMENTED` sin escribir nada. No se aplican resultados en esta fase.
