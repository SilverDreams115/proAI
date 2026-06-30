# R7.0 — Reporte post-jornada de aprendizaje

**Fecha:** 2026-06-24
**Rama:** `chore/production-polish`
**Alcance:** loop de aprendizaje sobre quinielas terminadas (read-only, sin entrenamiento automático).

> El sistema de aprendizaje post-jornada está **completo y operativo**, pero el
> aprendizaje real está **bloqueado por falta de resultados oficiales** para las
> slates objetivo. El loop quedará listo para aprender en el momento en que se
> carguen resultados oficiales validados.

---

## 1. Estado de las slates objetivo

| Slate    | Estado inventario        | Lineage                       | Predicciones | Resultados canónicos | Comparable | Bloqueo |
|----------|--------------------------|-------------------------------|--------------|----------------------|------------|---------|
| PG-2337  | `closed_pending_results` | `official_but_no_results_yet` | 14/14        | 0/14                 | ❌ No      | Sin resultados oficiales |
| PGM-800  | `closed_pending_results` | `official_but_no_results_yet` | 9/9          | 0/9                  | ❌ No      | Sin resultados oficiales |

Ambas slates tienen lineage oficial (promovidas desde guía LN) y predicciones
completas, pero **ningún resultado oficial ingerido todavía**. No son
comparables y no entran al dataset de aprendizaje.

Las slates activas actuales **PG-2338** y **PGM-801** permanecen en `NO JUGAR`
(Money Mode) y tampoco tienen resultados — no participan del aprendizaje.

---

## 2. Comparables

**Slates comparables: 0.** Ningún slate tiene cobertura canónica completa con
lineage oficial. (PG-2335 tiene lineage oficial pero solo 10/14 resultados →
`closed_partial_results`, no comparable.)

---

## 3. Aciertos

No hay aciertos comparables a reportar: 0 partidos comparables. El scoring
post-jornada existe y se ejecuta, pero devuelve `total=0` para las slates
objetivo porque no hay resultados oficiales contra los cuales comparar.

---

## 4. Errores principales

No clasificables todavía para PG-2337/PGM-800 (sin resultados). La capa de
atribución de errores (`learning_error_attribution_service`) está lista y
clasifica: `wrong_favorite`, `draw_underestimated`, `favorite_overestimated`,
`away_overestimated`, `guardrail_saved`, `guardrail_missed`, `canary_*`,
`money_mode_*`, `data_quality_issue`, `result_conflict`, etc.

---

## 5. Guardrails

- **Que salvaron:** N/A sin resultados comparables. La métrica `guardrail_saved`
  se calcula cuando un pick degradado (REVISAR/BLOQUEADO) coincide con un fallo.
- **Que fallaron:** N/A sin resultados comparables.
- **Money Mode:** PG-2338/PGM-801 en `NO JUGAR`; la corrección de esa decisión
  (`money_mode_correctly_blocked` vs `money_mode_too_conservative`) solo es
  evaluable una vez existan resultados.

---

## 6. Calibración

`audit_learning_calibration`: **bloqueada** — 0 muestras comparables. Mide
Brier / log-loss / ECE / top-1 / top-2 por banda de confianza, estado de
guardrail (ready / revisar / NO_SIMPLE), amistosos vs competición y por
competición, separando los vectores `raw` / `display` / `decision` / `effective`.
No entrena.

---

## 7. Dataset readiness

`audit_learning_dataset_readiness`:

- **training_ready = false**
- **Razón:** no comparable matches — no official results applied yet.
- **Mínimos faltantes:** ≥8 slates comparables (hay 0); ≥112 partidos
  comparables (hay 0).
- **Próxima acción de datos:** cargar resultados oficiales de una slate
  terminada (p.ej. PG-2337 / PGM-800) vía el CLI manual guardado, y re-ejecutar
  la auditoría.

---

## 8. ¿Se recomienda entrenar?

**No.** No hay evidencia comparable suficiente ni resultados oficiales. El
sistema **no entrena automáticamente** y no se marcará `training_ready=true`
mientras falten resultados, haya conflictos altos o pocas filas etiquetadas.

---

## 9. Próxima acción

1. Obtener resultados oficiales de PG-2337 y/o PGM-800 de una fuente confiable
   (TuLotero / Pronósticos / Lotería Nacional).
2. Construir el archivo manual seguro (`source: manual_official`, score por
   posición) y correr el dry-run:
   `python -m scripts.validate_completed_slate_results --manual-file results.json --dry-run`
3. Si `ready_to_apply=true` (cobertura 100 %, 0 conflictos, fuente high), aplicar
   con confirmación explícita:
   `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS`
4. Re-ejecutar inventory → score → attribution → calibration → dataset readiness.
5. Solo entonces, si la readiness lo permite, **proponer** (no ejecutar) un
   experimento de entrenamiento en shadow para revisión manual.

---

## Cómo correr el loop (read-only)

```bash
# Inventario de slates terminadas
python -m scripts.learning_inventory --all

# Validación de resultados (local + provider + manual)
python -m scripts.validate_completed_slate_results --draw-code PG-2337
python -m scripts.validate_completed_slate_results --manual-file results.json --dry-run

# Scoring post-jornada y atribución de errores
python -m scripts.score_completed_slate --draw-code PG-2337 --attribution
python -m scripts.score_completed_slate --all-comparable --json

# Auditorías
python -m scripts.audit_learning_calibration
python -m scripts.audit_learning_dataset_readiness
```

Endpoints read-only equivalentes bajo `/api/learning/…`:
`completed-slates/inventory`, `completed-slates/scores`, `slates/{id}/score`,
`slates/{id}/attribution`, `calibration`, `dataset-readiness`.
