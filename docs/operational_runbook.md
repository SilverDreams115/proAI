# Operational Runbook — Money Mode (R6.1)

Operación diaria para decidir **jugar / no jugar** cada quiniela Progol activa o
próxima. Todo el flujo es **read-only**: ningún paso de revisión escribe en la base
productiva. El sistema NO juega por ti — produce una decisión accionable que tú
ejecutas manualmente en Progol.

> **Estado al cierre de R6.1:** PG-2338 → **NO JUGAR**, PGM-801 → **NO JUGAR**.

---

## Reglas de oro (no negociables)

1. **Nunca jugar si Money Mode dice `NO JUGAR`.** Es una protección de capital, no una
   sugerencia.
2. **Nunca convertir un `NO SIMPLE` en simple.** El guardrail es autoritativo; si una
   posición no permite simple, va con cobertura (doble/triple) o no va.
3. **Nunca jugar una slate con metadata stale** (ver §"Detectar stale metadata").
4. **Nunca confiar en predicciones live si `money_mode_validation` bloquea** la slate
   (`data_blockers` no vacío).
5. **Nunca tocar la base productiva a mano** salvo un hotfix controlado y documentado.

---

## Operación diaria

### 1. Levantar el sistema
```bash
cd ~/projects/proAI
docker compose up -d proai postgres
docker compose up -d worker     # el worker hace archivado/observación, no juega
docker compose ps
```

### 2. Verificar readiness
```bash
curl -s http://127.0.0.1:8000/api/ready
# espera: {"status":"ready","ready":true,"database_ok":true,"schema_up_to_date":true}
```

### 3. Revisar slates activas
```bash
curl -s -H "Authorization: Bearer $PROAI_AUTH_API_KEY" http://127.0.0.1:8000/api/slates
```
Confirma que solo aparecen activas/próximas (no archivadas) y que los `match_count`
son los esperados.

### 4. Correr el comando operativo único
```bash
# dentro del contenedor (la DB vive en la red de docker):
docker compose exec --workdir /app/backend proai \
  python -m scripts.operate_money_mode --active-upcoming

# o, con el venv local apuntando a una DB alcanzable:
.venv/bin/python backend/scripts/operate_money_mode.py --active-upcoming
```
Variantes:
```bash
... operate_money_mode.py --draw-code PG-2338
... operate_money_mode.py --active-upcoming --json
... operate_money_mode.py --active-upcoming --markdown /tmp/money_mode_report.md
```

El reporte imprime por slate: `SLATE`, `STATUS`, `DECISION`, `RECOMMENDED TICKET`,
`DO_NOT_SIMPLE`, `WARNINGS`, `WRITE_SAFETY`, y al final `COUNTS_DELTA` + auditoría de
write-safety.

### 5. Leer la decisión final
- **`JUGAR …`** → procede al paso 6.
- **`NO JUGAR`** → no juegas esa slate. Fin para esa slate (paso 7).

### 6. Si JUGAR — usar el boleto recomendado
- Usa exactamente el boleto marcado `RECOMMENDED` (balanceado por defecto, conservador
  si hay riesgo medio/alto).
- Respeta todas las posiciones `DO_NOT_SIMPLE`: van con cobertura, nunca como fijo.
- Las combinaciones/costo del boleto están en el detalle de Money Mode RC.

### 7. Si NO JUGAR — no jugar esa slate
- Documenta el motivo (`reason`) y sigue. No fuerces una jugada.

### 8. Confirmar counts delta cero
El propio `operate_money_mode` reporta `COUNTS_DELTA : ZERO`. Para una verificación
independiente:
```bash
docker compose exec -T postgres psql -U proai -d proai -At -c "
SELECT 'predictions='||count(*) FROM predictions
UNION ALL SELECT 'ticket_recommendation_snapshots='||count(*) FROM ticket_recommendation_snapshots
UNION ALL SELECT 'match_feature_snapshots='||count(*) FROM match_feature_snapshots;"
```
antes y después: deben ser idénticos.

### 9. UI de estado
Abre la pestaña **Diagnóstico** → panel **Operational Money Mode Status**: muestra
JUGAR / NO JUGAR por slate, Money Mode ready, última validación y write-safety. Un
`NO JUGAR` nunca se oculta.

---

## Cómo rollbackear el canary local

El canary es local y reversible por flag (no toca el ticket real). Para apagarlo:
```bash
# en .env
PROAI_TEAM_RATING_CANARY_ENABLED=false
docker compose up -d proai     # recrea solo proai
```
Para reducir el scope sin apagarlo, ajusta `PROAI_TEAM_RATING_CANARY_POSITIONS` /
`PROAI_TEAM_RATING_CANARY_SCOPE` y recrea `proai`. El canary nunca debe ampliarse
fuera de `active_upcoming` + posiciones gated.

---

## Cómo detectar stale metadata

`money_mode_validation` (incluido en `operate_money_mode` y en el endpoint) reporta:
- `data_blockers`: `slate_archived`, `no_matches`, `non_contiguous_positions`,
  `placeholder_teams_at_*`, `no_predictions_available`.
- `warnings`: `live_predictions_only`, `registration_closed`, `no_registration_cierre`.

Señales de stale a vigilar:
- `prediction_status = pending/missing` en una slate activa → predicciones no
  disponibles.
- `placeholder_teams_at_*` → fixtures sin resolver.
- `non_contiguous_positions` → composición incompleta.
- `registration_closed` en una slate que debería estar abierta → cierre vencido o reloj
  desfasado.

Si hay cualquier `data_blocker`, la slate **no es jugable**: `money_mode_ready=false` y
la decisión cae a `NO JUGAR`.

---

## Cómo validar una nueva slate activa/próxima

Cuando entra una quiniela nueva, `active_slate_scope` la detecta automáticamente (no
archivada + cierre futuro). Para validarla:
```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.operate_money_mode --draw-code <DRAW_CODE>
```
Checklist rápido:
1. Aparece en `/api/slates` como activa (no archivada).
2. `match_count` correcto (14 weekend / 9 midweek típico).
3. `prediction_status` = `persisted` o `live_available`.
4. `data_blockers` vacío.
5. `operate_money_mode` produce una decisión y `COUNTS_DELTA : ZERO`.

Las futuras slates heredan automáticamente toda la política (`active_upcoming`).

---

## Qué NO hace este flujo (por diseño)

no full activation · no training · no optimizer productivo · no ticket integration
real · no escribe tickets/predicciones/feature snapshots · no results apply · no
API-Football online · no cambia probabilidades ni recomendaciones persistidas. Cualquier
intento de escribir DB productiva o activar el ticket real es un **stop inmediato**.

---

## R6.3 — Performance, resultados externos y readiness

### Carga rápida de la UI
- El tablero de predicción carga **sin esperar** los paneles pesados. Money
  Mode, los dry-runs canary, el estado operativo y los resultados externos se
  cargan **lazy** al abrir la pestaña **Diagnóstico**, con **cache por slate**
  (re-abrir una slate es instantáneo) y **cancelación** de respuestas viejas.
- Endpoint ligero para primer pintado: `GET /api/operations/dashboard-fast`
  (solo slates activas + sugerencia + validación; no computa Money Mode).

### Resultados externos (fuente gratuita)
- Ver `docs/free_results_provider.md`. Panel **Resultados externos** en
  Diagnóstico (solo lectura). Probe:
  `python -m scripts.probe_free_results_source --provider football_data_org --active-upcoming`.
- **Nunca** se aplican resultados automáticamente. Apply manual bloqueado
  (`scripts/apply_provider_results.py`, requiere `--apply --confirm
  APPLY-PROVIDER-RESULTS-ONLY` + flags de habilitación; en R6.3 responde
  NOT IMPLEMENTED).

### Readiness sin falsear confianza
- Ver `docs/readiness_expansion.md`. Auditoría:
  `python -m scripts.audit_ready_expansion --active-upcoming`.
- Regla: **nunca** promover a READY sin evidencia real. Estado actual: sin
  promociones seguras (amistosos de baja evidencia).

### Operativo integrado
- `operate_money_mode.py --active-upcoming` ahora incluye
  `readiness_expansion_summary` y `performance_note` por defecto (rápido), y el
  estado del proveedor solo con `--with-results-provider` (sin red salvo que el
  proveedor esté habilitado).

---

## R6.4 — Opciones por slate, pricing y validación de slates terminadas

### Opciones de boleto (siempre visibles)
- Ver `docs/progol_pricing_and_options.md`. Aunque Money Mode diga NO JUGAR, el
  panel **Opciones de boleto** muestra agresiva/balanceada/conservadora/manual
  como simulaciones no recomendadas, con combinaciones y costo.
- Precio **no verificado** por defecto → costo "no verificado" (nunca $0).
- CLI: `python -m scripts.audit_slate_options --active-upcoming`.
- Probe pricing: `python -m scripts.probe_progol_pricing`.

### Validación de slates terminadas
- Panel **Validación de resultados** + endpoints
  `GET /api/tracking/completed-slates/results-validation` y
  `/api/tracking/slates/{id}/results-validation`.
- CLI: `python -m scripts.validate_completed_slate_results --draw-code PG-2337`
  (o `--all-completed`). Solo lectura; reporta coverage, conflictos y qué falta.

### Backfill post-jornada de resultados oficiales (proceso estándar, R7.1)

El proveedor gratuito no cubre todas las ligas del programa (Sudamericana,
ligas argentina/colombiana/chilena, Liga MX, MLS, Série B) y en eliminatorias
reporta marcadores con tiempo extra. La vía canónica para cerrar una jornada
en el learning loop es SIEMPRE el flujo manual-oficial:

1. **Cadena oficial**: tomar la combinación ganadora del concurso en
   `loterianacional.gob.mx` (Progol y Progol/Resultados listan los últimos 15
   sorteos con su secuencia L/E/V). Esa cadena es la fuente de verdad del signo.
2. **Plantilla**: `python -m scripts.make_manual_results_template --draw-code
   PG-XXXX` (fixtures reales de la slate en `source_note`).
3. **Scores**: llenar `sign` + `score` por posición desde fuentes verificables
   (FIFA/ligas oficiales). Regla Progol: el signo cuenta el **tiempo regular
   (90')**; si hubo prórroga/penales el `score` a capturar es el de 90'
   (p.ej. Bélgica 3-2 Senegal en prórroga ⇒ `E` / `2-2`). El dry-run del
   proveedor sirve como cross-check, nunca como fuente única.
4. **Placeholders**: si una posición quedó ligada a un slot de bracket
   ("Ganador X"), relinkear ANTES de aplicar:
   `python -m scripts.relink_slate_team --draw-code PGM-803 --position 4
   --side away --target-team "Bélgica" --dry-run` (apply con
   `--apply --confirm RELINK-SLATE-TEAM`). In-place, preserva PK/predicciones.
5. **Validar y aplicar**: `python -m scripts.validate_completed_slate_results
   --manual-file <archivo> --dry-run` debe dar `ready_to_apply=true` y 0
   blockers; después `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS`.
6. **Verificar**: `GET /api/learning/dataset-readiness` debe sumar la slate a
   `comparable_slates`.

La cadena derivada de los scores DEBE coincidir 100% con la cadena oficial del
paso 1 antes de aplicar; cualquier desviación es señal de fixture mal mapeado
(no aplicar: investigar/relinkear). Los apply automáticos de proveedor
(`apply_provider_results.py`, `apply_completed_slate_results.py`) siguen
intencionalmente inertes.
