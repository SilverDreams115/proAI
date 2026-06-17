# proAI — Calidad de Datos

## Resultados canónicos

Un resultado canónico es el resultado final confirmado de un partido, almacenado en `canonical_results`. Es la fuente de verdad para scoring y adaptive dataset. Sin resultado canónico confirmado, no se debe computar scoring.

### Fuentes y prioridad

`result_source_priority` en `SourceModel` determina qué fuente prevalece cuando múltiples connectors reportan resultados distintos para el mismo partido. Fuentes con mayor prioridad sobreescriben a las de menor prioridad.

### Conflictos de resultados

Un conflicto ocurre cuando dos fuentes con igual o mayor prioridad reportan resultados distintos para el mismo `(home_team_id, away_team_id, match_date)`. Los conflictos se rastrean en el dataset adaptativo vía `conflict_rate`.

**No usar resultados conflictivos en scoring ni en retraining.** El gate `max_conflict_rate=0.05` en `AdaptiveRetrainingService` rechaza datasets con más del 5% de filas conflictivas.

---

## Decisiones de slate legacy (PG-2334 → PG-2336)

### PG-2334
Primer slate producido. Patrón de desacoplamiento detectado: la slate recibió múltiples snapshots de predicción en versiones diferentes. El `composition_hash` cambió durante el proceso, generando versiones `v1`, `v2`, etc. Resultado: métricas de scoring potencialmente inconsistentes entre versiones.

**Lección:** el `composition_hash` garantiza que cada score se compare contra la composición exacta que generó la predicción. No mezclar scores de versiones distintas.

### PG-2335
Segunda slate analizada. Revisión post-mortem identificó tres posiciones donde el modelo tenía sesgos por calibración insuficiente. Los artefactos de modelo fueron ajustados (`model_training_artifacts.py` línea 57). Slate marcada como `PARTIAL_ONLY` — datos parcialmente útiles para el adaptive dataset pero no representativos del pipeline actual.

### PG-2336
Primera slate limpia con el pipeline completo:
- `composition_hash` desde el inicio
- modelo calibrado
- evidence quality documentada
- anchor gap diagnostic activo
- 0 partidos `blocked`

Es la primera slate válida para alimentar el adaptive dataset sin restricciones. **No modificar manualmente ningún dato de PG-2336.**

### Por qué no hacer backfill legacy arbitrario

Hacer backfill de PG-2334 o PG-2335 requiere:
1. Conocer exactamente qué modelo estaba activo en cada momento
2. Confirmar que los features usados son los mismos que los actuales
3. Resolver conflictos de resultados documentados
4. Evitar contaminar el adaptive dataset con predicciones de versiones del modelo incompatibles

Sin esas garantías, el backfill introduce ruido en el retraining y distorsiona las métricas walk-forward.

---

## Evidence quality

`evidence_count` es el número de items de evidencia de texto (noticias, lesiones, rotaciones, etc.) asociados a un partido. Actualmente es **0 en casi todos los partidos** porque no hay scraper de noticias activo.

Esto es intencional y declarado en el sistema. El `_confidence_band()` acepta H2H o forma reciente como anchor equivalente (`evidence_count >= 1 OR h2h >= 2 OR (home_recent >= 3 AND away_recent >= 3)`). No inflar `evidence_count` artificialmente ni crear evidencias sintéticas para subir bandas de confianza.

---

## Anchor gap

El anchor gap es el diagnóstico que explica por qué un partido está en banda `low`. Se reporta cuando `_anchored = False`:

```
anchored = (evidence_count >= 1) OR (h2h >= 2) OR (home_recent >= 3 AND away_recent >= 3)
```

Si `anchored = False`, el rationale incluye exactamente qué falta:

| Condición | Mensaje |
|---|---|
| `home_recent < 3` | "Local tiene N resultado(s) reciente(s) en ventana activa — necesita 3" |
| `away_recent < 3` | "Visitante tiene N resultado(s) reciente(s) en ventana activa — necesita 3" |
| `h2h < 2` | "Historial directo insuficiente (N enfrentamiento(s), necesita 2)" |

### Ventana activa

La ventana de forma reciente es **3 × mediana de días entre partidos** de la competición. Para "International Friendlies" (al que se mapean las eliminatorias WCQ): ≈211 días.

Partidos fuera de la ventana no contribuyen a `home_recent` ni `away_recent`. Esto es correcto: un partido de hace 8 meses no refleja la forma actual del equipo.

### Por qué los low no deben inflarse artificialmente

Un partido `low` significa que el modelo no tiene suficiente evidencia reciente para respaldar su elección. Convertirlo a `medium` o `high` mediante regla artificial (e.g. "si la competición es copas del mundo, dar +1 de bonus") viola la semántica del sistema y produce picks no auditables.

La UI muestra el anchor gap diagnostic explícitamente para que el operador entienda la limitación, no para ocultarla.

---

## Datos de WCQ (World Cup Qualifying)

Las fuentes de calificatorias mundiales están registradas en el sistema:

| Confederation | League ID TSDB | Estado (jun 2026) |
|---|---|---|
| CONMEBOL | 5515 | Terminó sep 2025 — fuera de ventana activa |
| CAF | 5514 | Terminó oct 2025 — fuera de ventana activa |
| AFC | 5513 | Terminó jun 2025 — fuera de ventana activa |
| CONCACAF | 5516 | Terminó mar 2026 — dentro de ventana |
| UEFA | 5518 | Activa 2026 |

Los aliases de normalización (`eliminatorias mundialistas`, `wcq`, etc.) mapean a `international-friendlies` para compartir la ventana de forma de ese grupo de competiciones.

---

## Datos faltantes vs. datos conflictivos

| Situación | Comportamiento correcto |
|---|---|
| Sin datos recientes (`home_recent < 3`) | `low` — mostrar anchor gap |
| Sin H2H (`h2h < 2`) | `low` — mostrar anchor gap |
| Competición no clasificada | `blocked` — no presentar pick |
| Resultados conflictivos en DB | No usar en scoring ni retraining |
| Evidence count = 0 | Normal — no inflar artificialmente |
