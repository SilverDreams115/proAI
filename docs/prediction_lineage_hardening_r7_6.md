# R7.6 — Prediction Lineage Hardening

**Fecha:** 2026-06-24 · **Rama:** `chore/production-polish` · **HEAD previo:** `b4b4d0f`
**Alcance:** endurecer el pipeline para que **ninguna predicción futura persistida
quede ciega**. No entrena, no recalibra, **no backfillea** predicciones antiguas.

> Los registros históricos ciegos se mantienen intactos para no alterar
> evidencia previa. La corrección aplica a predicciones **futuras**.

---

## 1. Problema detectado: slates ciegas

Auditoría read-only de las 2177 predicciones persistidas:

| | total |
|---|--:|
| predicciones | 2177 |
| con `slate_id` | 1365 (sin: **812**) |
| con `sanity_audit_json` | 336 (sin: **1841**) |
| con `composition_hash` | 1365 (sin: 812) |
| con raw/display/decision | 336 (sin: 1841) |
| **persistibles bajo la nueva política** | **336** (ciegas: **1841**) |

Por draw_code:

| draw_code | total | sanity_audit | slate_id | persistable |
|-----------|------:|-------------:|---------:|------------:|
| (sin slate_id) | 812 | 0 | 0 | 0 |
| PG-2336 | 742 | 0 | 742 | 0 |
| PG-2337 | 392 | 294 | 392 | 294 |
| PG-2338 | 42 | 42 | 42 | 42 |
| PGM-799 | 90 | 0 | 90 | 0 |
| PGM-800 | 99 | 0 | 99 | 0 |

Solo PG-2337 (parcial) y PG-2338 (completo) tienen lineage. El resto es ciego:
sin `sanity_audit` (PG-2336/PGM-799/PGM-800) o incluso sin `slate_id` (812 filas,
incluye PG-2335).

## 2. Causa probable

Predicciones generadas por code paths/épocas previas que (a) no enlazaban
`slate_id`/`composition_hash`, o (b) no persistían el `sanity_audit_json`. No
había **contrato** que obligara la trazabilidad antes de escribir, así que una
fila incompleta se guardaba en silencio.

## 3. Contrato nuevo de lineage

Nuevo módulo `app/domain/prediction_lineage.py`:

- `check_prediction_lineage(...) -> LineageCheck` — no-lanza; devuelve los campos
  faltantes (para read-only y la auditoría).
- `assert_prediction_lineage_complete(...)` — lanza `PredictionLineageError` antes
  de escribir, con mensaje explícito `Prediction lineage incomplete: missing <campos>`.

## 4. Campos obligatorios (para persistir)

```
match_id, slate_id, composition_hash, slate_version, recommended_outcome
sanity_audit_json con:
  raw_probabilities, display_probabilities, decision_probabilities  (dicts no vacíos)
  final_status, evidence_level, sanity_policy_version
  model_artifact_id  O  fallback_used (lineage de modelo)
```

`effective_probabilities` (canary) es **opcional**: no se exige porque solo existe
cuando el canary sirve la predicción en vivo; en predicciones persistidas normales
queda ausente por diseño (documentado, no es un hueco de lineage).

## 5. Dónde se integró

`PredictionService._persist_prediction_audit` (único punto de persistencia de
predicciones en todo el backend — verificado por grep). La aserción corre
**después** de los early-returns de no-persistencia (stubs/read-only) y **antes**
del `try/except` best-effort, para que una violación de lineage **surja fuerte** en
lugar de tragarse como un error transitorio de auditoría.

## 6. Cómo falla si falta lineage

- **Producción (persist):** `PredictionLineageError: Prediction lineage incomplete:
  missing <campo>` → no se escribe la fila ciega.
- **Read-only / live no-persistente:** el compute con `persist_audit=False` nunca
  llega al guard (no persiste); `check_prediction_lineage` permite marcar
  `lineage_incomplete` in-memory sin lanzar.
- **Tests:** falla fuerte (`pytest.raises(PredictionLineageError)`).

## 7. Auditoría actual de registros viejos

Ver §1. **336 persistibles / 1841 ciegas.** Reportado, no reparado.

## 8. Qué NO se backfilleó

- ❌ No se modificó ninguna predicción histórica.
- ❌ No se rellenó `sanity_audit_json`, `slate_id` ni `composition_hash` retroactivo.
- ❌ No se regeneraron predicciones.
- Delta `predictions` = **0** (2177 → 2177).

## 9. Riesgos mitigados

- Predicciones futuras ciegas (sin trazabilidad) → **imposibles de persistir**.
- Atribución de errores / calibración ciega para slates nuevas → evitada de raíz.
- Pérdida silenciosa de linaje de modelo (artifact/fallback) → bloqueada.

## 10. Próxima acción

- Las nuevas predicciones (p.ej. próximas jornadas activas) nacerán con lineage
  completo automáticamente vía el flujo de `build_slate_predictions`.
- Las históricas ciegas seguirán excluidas del dataset comparable hasta que tengan
  resultados; su falta de sanity_audit es un límite conocido (documentado en R7.4/R7.5).
- **No** se entrena ni recalibra hasta alcanzar el umbral (≥8 slates / ≥112 partidos).

## Confirmación

no training · no recalibración · no regeneración masiva · no ticket writes ·
no match_results writes · **no schema changes** (todas las columnas ya existían) ·
no backfill · no API-Football · slates históricas intactas · futuras predicciones
protegidas por el contrato de lineage.
