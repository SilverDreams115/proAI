# proAI — ML Pipeline

## Modelo de producción

El modelo de producción es **XGBoost** (CPU-only). Es la única librería ML permitida en el runtime. scikit-learn está explícitamente excluido.

El modelo produce probabilidades `P(home) / P(draw) / P(away)` para cada partido de una slate Progol. Antes de XGBoost, se aplica un ajuste Poisson-Dixon para calibrar el peso del empate según el ritmo de goles del partido específico.

XGBoost solo se usa en las competiciones donde el veredicto walk-forward publicado (`/data/backtest_history/index.json`, campo `xgboost_beats_heuristic`) lo aprueba; el resto rutea al **blend heurístico**: Elo + Poisson Dixon-Coles + perfil de equipo. En ese blend, Elo y perfil votan solo el reparto local-vs-visitante y el grid Dixon-Coles es dueño de la masa del empate — mezclar sus masas completas y renormalizar diluía E sistemáticamente (~0.30 → ~0.23), el hallazgo de la comparación contra mercado de 2026-07-16.

### Retrain periódico del artefacto base

El worker reentrena `elo_poisson_blend` cuando el último run en DB es más viejo que `PROAI_MODEL_RETRAIN_INTERVAL_HOURS` (default 24; 0 lo desactiva). El gate es el `trained_at` del run — no memoria del worker — así que los reinicios no lo redisparan. Esto mantiene frescos ratings, lambdas y curvas de calibración sin intervención del operador (el gap mayo→julio de 2026 apareció como sobreconfianza en partidos con forma reciente distinta). Es independiente del *adaptive retraining gate* de abajo, que aprende de jornadas Progol completas.

---

## Features de alto nivel

Las features se construyen en `FeatureService` a partir de datos históricos de resultados y estadísticas:

- **Forma reciente** de local y visitante (ventana = 3 × mediana de días entre partidos de la competición)
- **Head-to-head** histórico entre los dos equipos
- **Ratios de goles** anotados y recibidos por equipo
- **Elo ratings** derivados del historial
- **Indicadores de competición** (liga, copa, clasificatorio)
- **Evidence count** (señales de contexto de texto — actualmente 0 por defecto porque no hay scraper de noticias activo)

---

## Cómo se genera una predicción

1. `PredictionService.predict_for_slate()` se llama con la slate activa.
2. Para cada partido: `FeatureService` construye el feature vector.
3. Se evalúa `_has_insufficient_data()` — si los anchors no alcanzan el mínimo, `confidence_band = "blocked"`.
4. Si la competición está en `PROAI_LIVE_PICK_BLOCKED_COMPETITIONS`, también `"blocked"`.
5. Si pasa los gates: XGBoost produce probabilidades crudas → ajuste Poisson → banda de confianza.
6. Se calcula `composition_hash` y `slate_version` de la slate al momento del snapshot.
7. El resultado se persiste en `predictions` + `prediction_snapshots`.

### Bandas de confianza

```
anchored = (evidence_count >= 1) OR (h2h >= 2) OR (home_recent >= 3 AND away_recent >= 3)

"blocked"  → competición no clasificada, O datos insuficientes (total_anchors < 4, AND
              NOT (ambos lados >= 2 recientes O h2h >= 3))
"high"     → top_prob >= 0.55 AND spread >= 0.12 AND anchored
"medium"   → top_prob >= 0.40 AND spread >= 0.02 AND anchored
"low"      → cualquier otro caso (anchored o no, sin threshold mínimo)
```

Knockouts (partido sin empate posible, redistribución E=0):
```
"high"   → top_prob >= 0.55 AND anchored
"medium" → top_prob >= 0.50
"low"    → resto
```

**Regla no negociable:** los thresholds no se relajan para inflar bandas. Un partido con datos insuficientes se muestra como `low` o `blocked` — nunca como `medium` o `high` por regla artificial.

---

## Anchor gap diagnostic

Cuando un partido queda en `low` por falta de anclaje, `_build_rationale()` incluye una descripción de exactamente qué falta:

- Local tiene N resultado(s) reciente(s), necesita 3
- Visitante tiene N resultado(s) reciente(s), necesita 3
- Historial directo insuficiente (N enfrentamiento(s), necesita 2)

Este diagnóstico se expone en la UI y en el endpoint `/api/predictions/slates/{id}/quality`.

**Por qué ocurre con calificatorias:** la ventana activa es 3 × mediana de días entre partidos (≈211 días para "International Friendlies"). Las eliminatorias CONMEBOL terminaron en septiembre 2025, CAF en octubre 2025 — ambas fuera de la ventana para partidos del 12 de junio de 2026.

---

## Cómo se genera un ticket

1. `TicketRecommendationService` recibe las predicciones del slate.
2. `TicketOptimizer` selecciona el pick óptimo (1/X/2) por partido según las probabilidades y el objetivo de cobertura.
3. `coverage.py` (Poisson Binomial) calcula P(≥K aciertos) dado el conjunto de picks.
4. El ticket se presenta como `Simple` (14 picks únicos), `Dobles` (con segunda opción en partidos seleccionados), y `Completa`.

---

## Scoring

Después de que los partidos se juegan, `JornadaScoringService` computa:

- **Hit-rate**: fracción de picks correctos
- **Brier score**: pérdida cuadrática de las probabilidades predichas vs resultado real

El scoring se vincula a `(slate_id, composition_hash)` para garantizar que se compare contra exactamente la misma composición que generó la predicción.

**No ejecutar scoring antes de tener resultados canónicos confirmados.** Ver `docs/data_quality.md`.

---

## Adaptive dataset

`AdaptiveDatasetService` ensambla filas de entrenamiento a partir de jornadas completas con resultados canónicos, predicciones guardadas, y picks de ticket. Cada fila tiene:

- feature vector en el momento de la predicción
- resultado real
- pick del ticket
- hit/miss

Este dataset alimenta el retraining gate.

---

## Adaptive retraining gate

`AdaptiveRetrainingService` evalúa readiness con estos gates por defecto:

| Gate | Valor por defecto |
|---|---|
| `min_trainable_rows` | 50 |
| `min_complete_slates` | 3 |
| `max_conflict_rate` | 5% |
| `max_blocked_rate_for_full_retrain` | 60% |
| `min_new_rows_since_last_train` | 30 |

**Flujo obligatorio antes de reentrenar:**
1. `GET /api/training/adaptive/readiness` — verificar que todos los gates pasan
2. `POST /api/training/adaptive/dry-run` — simular sin persistir
3. `POST /api/training/adaptive/run` — ejecutar solo si los gates y el dry-run son satisfactorios

**No ejecutar `/run` si algún gate falla.** El endpoint devuelve 409 si la readiness no pasa.

---

## Neural baseline experimental

`NeuralBaselineService` implementa un MLP de 2 capas ocultas en PyTorch puro, sin sklearn. Características de diseño:

- `is_production = False` en todos los artefactos que escribe
- `model_type = "neural_baseline_experimental"`
- Nunca escribe en las tablas de predicción de producción
- Solo accesible via `/api/training/neural/readiness` (GET) y `/api/training/neural/dry-run` (POST)

**Cuándo podrá entrar en producción:** cuando haya suficientes jornadas completas con resultados canónicos para validar que supera al XGBoost en walk-forward. Las métricas comparativas están en el endpoint de dry-run. Hoy el readiness es `skip` (0 jornadas completas disponibles a junio 2026).

---

## Qué NO hacer con el ML pipeline

| Acción prohibida | Razón |
|---|---|
| Relajar thresholds de confianza para inflar bandas | Produce picks no fundamentados |
| Convertir `low` a `medium`/`high` por regla artificial | Viola la semántica de las bandas |
| Reentrenar con slates contaminadas o con conflict_rate alto | Introduce ruido en el modelo |
| Usar resultados conflictivos en scoring | Genera métricas incorrectas |
| Entrenar neural baseline en producción sin validación walk-forward | Sin evidencia de mejora, puede degradar picks |
| Backfill legacy arbitrario de PG-2334/PG-2335 | Ver `docs/data_quality.md` |
