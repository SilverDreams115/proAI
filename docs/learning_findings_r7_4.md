# R7.4 — PG-2335 Official Results Intake (Comparable Slate Expansion)

**Fecha:** 2026-06-24 · **Rama:** `chore/production-polish` · **HEAD:** `df8b9a2`
**Alcance:** intentar convertir PG-2335 en la 3ª slate comparable. Read-only salvo
apply guardado (no ejecutado). **No entrena, no recalibra, no toca guardrails.**

---

## 1. Estado PG-2335

| campo | valor |
|-------|-------|
| slate_id | `71d1d446-0ceb-4152-a35e-b8b1461e056b` |
| week_type | weekend |
| state | `closed_partial_results` |
| match_count | 14 |
| prediction_count | 14 |
| local/canonical_result_count | **10 / 14** |
| conflicts | 0 |
| comparable | **false** |
| blockers | missing_provider_results, incomplete_coverage, incomplete_canonical_results |

Lineage oficial (LN) confirmado. Tiene 10 resultados ya almacenados y predicciones
completas, pero **faltan 4 resultados** → no comparable.

## 2. ¿Comparable?

**No.** PG-2335 sigue en `closed_partial_results`. Faltan 4 de 14 resultados.

## 3. ¿Se aplicaron resultados?

**No.** No había fuente oficial completa para los 4 partidos faltantes en esta
fase. **Estado final: B — bloqueada con plantilla manual completa lista para llenar.**

### Búsqueda de resultados oficiales
- **Archivos locales:** solo `backend/tests/fixtures/progol_guia_2335.txt` (guía de
  alineaciones, **no** resultados) y el conector `progol_resultados.py`. Ningún
  archivo de resultados.
- **Provider football_data_org:** `status=disabled`, cobertura 0/14 (consulta online
  no permitida).
- **Captura del usuario:** no se proporcionó para PG-2335 en esta fase.
- Clasificación: **D/E — sin resultados oficiales completos / fuente no configurada.**

### Posiciones que faltan llenar (4)
| pos | partido | pred actual |
|----:|---------|:--:|
| 2 | Paris SG vs Arsenal | L |
| 3 | Toluca vs Tigres | L |
| 4 | Tampico Madero vs Tepatitlán | L |
| 12 | Avaí vs Criciúma | E |

Las otras 10 posiciones **ya tienen resultado** almacenado. Para aplicar por la vía
manual (que exige coverage 100% del archivo), el operador debe rellenar las **14**
posiciones: las 10 existentes con los mismos signos ya almacenados (si difieren, el
guard `result_conflict` bloqueará) y las **4 nuevas** (2, 3, 4, 12) desde fuente
oficial.

### Plantilla creada
`docs/manual_results_templates/pg2335_results_template.json` — 14 posiciones con el
fixture real en `source_note`, `sign`/`score` en null. Verificación de rechazo:

```
provided 14/14 · coverage 100% · ready_to_apply: False
blockers: ['missing_score', 'result_conflict']
```

Rechazada por campos faltantes (no por estructura). El `result_conflict` aparece
porque los signos null no coinciden con los 10 resultados ya almacenados; desaparece
al rellenar correctamente.

## 4. Scoring PG-2335

N/A — no comparable. (Cuando se complete y aplique, el scoring quedará disponible
vía `score_completed_slate --draw-code PG-2335`.)

## 5. Comparables totales

**2** — PG-2337 (14) y PGM-800 (9) = **23 partidos**. Sin cambios respecto a R7.3.

## 6. Calibration update

Sin cambios (no se aplicaron resultados): 51 muestras / 2 slates. `display` mejor
calibrado (ECE 0.077) que `decision` (ECE 0.18). Política UI=display, decisión=decision
se mantiene.

## 7. Dataset readiness update

| métrica | valor |
|---------|------:|
| training_ready | **false** |
| slates comparables | 2 |
| partidos comparables | 23 |
| mínimo faltante | ≥8 slates (hay 2); ≥112 partidos (hay 23) |
| PG-2335 | excluida: `incomplete_results (10/14 canonical, 0 conflicts)` |

## 8. Sanity audit coverage

| slate | predicciones | con sanity_audit (final_status) | slate_id |
|-------|--:|--:|---|
| PG-2337 | 392 | **294** | seteado |
| PGM-800 | 99 | **0** (ciego) | seteado |
| PG-2335 | 756 (por match_id) | **0** (ciego) | **NULL en todas** |

Hallazgo: **PGM-800 y PG-2335 no tienen sanity_audit** → atribución de errores y
calibración por estado quedan ciegas (`guardrail=unknown`). Además, las predicciones
de PG-2335 **ni siquiera tienen `slate_id`** (se enlazan solo por `match_id` vía el
fallback del scorer). Solo PG-2337 está completo.

**No se backfilleó nada en esta fase.** Recomendación para evitar predicciones
futuras ciegas: el pipeline de predicción debe persistir siempre `sanity_audit_json`
y enlazar `slate_id` al generar predicciones de slate.

## 9. Próxima acción

1. Conseguir resultados oficiales de los 4 partidos faltantes de PG-2335
   (pos 2, 3, 4, 12) desde Pronósticos/TuLotero.
2. Rellenar `docs/manual_results_templates/pg2335_results_template.json` (14 pos,
   las 10 existentes consistentes + las 4 nuevas).
3. `validate_completed_slate_results --manual-file … --dry-run` → `ready_to_apply:true`.
4. `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS` → PG-2335 comparable (3ª slate).
5. Re-correr scoring / calibration / dataset readiness.

## 10. Qué NO cambiar todavía

- ❌ No entrenar (2 slates / 23 partidos << umbral 8/112).
- ❌ No recalibrar, no cambiar thresholds, no Money Mode más agresivo.
- ❌ No bajar guardrails ni convertir NO_SIMPLE en SIMPLE.
- ❌ No backfillear sanity_audit ni slate_id en esta fase (es trabajo de pipeline,
  con su propia revisión).
- ❌ No tocar canary, pricing ni optimizer.
