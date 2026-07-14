# R7.5 — Apply PG-2335 Official Results (3rd Comparable Slate)

**Fecha:** 2026-06-24 · **Rama:** `chore/production-polish` · **HEAD previo:** `21bf21d`
**Alcance:** aplicar resultados oficiales completos de PG-2335 (captura del usuario)
y actualizar scoring/calibración/readiness. Apply guardado; **no entrena, no
recalibra, no toca guardrails/Money Mode.**

---

## 1. Resumen

PG-2335 pasó de `closed_partial_results` (10/14) a **`closed_comparable` (14/14)**.
Ahora hay **3 slates comparables / 37 partidos**. El gate de entrenamiento sigue
**cerrado** (`training_ready=false`, umbral ≥8 slates / ≥112 partidos).

## 2. Apply

| campo | valor |
|-------|-------|
| dry-run | coverage 100% · ready_to_apply **true** · conflicts **0** · 14/14 · source high |
| checksum | `8ed62c329be3e83a50681572fb25a1c20c2362310982baad46a87fcf1206a3d9` |
| source | Manual Official Progol Results (priority 30) |
| posiciones aplicadas | 1–14 (14) |
| **match_results delta** | **+14** (15172 → 15186) |

**Explicación del delta +14 (no +4):** la vía manual-official registra un
resultado oficial por posición (14 filas), no solo rellena los 4 huecos. Las 10
filas previas (otra fuente) **coexisten y coinciden** con la captura oficial — el
dry-run confirmó `conflicts=0`, así que el resultado canónico queda limpio
(canonical prefiere la fuente manual, prioridad 30). Solo cambió `match_results`.

Las 4 posiciones que faltaban (2 Paris SG-Arsenal=E, 3 Toluca-Tigres=E,
4 Tampico-Tepatitlán=L, 12 Avaí-Criciúma=V) ahora tienen resultado; las otras 10
se confirmaron consistentes con lo ya almacenado.

## 3. Scoring PG-2335

**hits 6/14 (0.429)** · top1 6 · top2_cov 10/14 · brier 0.807 · logloss 4.90.

| error_type | n |
|------------|--:|
| correct | 6 |
| draw_underestimated | 4 |
| favorite_overestimated | 3 |
| away_overestimated | 1 |

Money Mode (slate NO JUGAR): 8 fallos correctamente bloqueados, 6 aciertos
bloqueados de más. `guardrail=unknown` en las 14 (PG-2335 **no tiene
sanity_audit** — predicciones ciegas, ver §6).

Patrón de error fuerte: **empates subestimados** (4) — pos2 PSG-Arsenal y pos3
Toluca-Tigres terminaron E con p(empate)≈0.0 → log-loss altísimo (4.90). El modelo
prácticamente excluyó el empate en partidos parejos.

## 4. Global comparable (37 partidos)

| slate | hits | rate | brier | logloss |
|-------|------|-----:|------:|--------:|
| PG-2337 | 9/14 | 0.643 | 0.536 | 1.106 |
| PGM-800 | 5/9 | 0.556 | 0.689 | 1.420 |
| **PG-2335** | **6/14** | **0.429** | **0.807** | **4.900** |
| **total top-1** | **20/37 (0.541)** | | | |

PG-2335 es la peor de las tres — arrastra brier/logloss globales por la
subestimación de empates.

## 5. Calibration update

| vector | n | brier | logloss | ECE | top1 | top2 |
|--------|--:|------:|--------:|----:|-----:|-----:|
| raw | 14 | 0.536 | 1.106 | 0.136 | 0.643 | 0.786 |
| **display** | 14 | 0.564 | 1.066 | **0.077** | 0.643 | 0.786 |
| decision | 37 | 0.676 | 2.618 | 0.212 | 0.541 | 0.757 |
| effective | 0 | — | — | — | — | — |

`decision` empeoró al sumar PG-2335 (logloss 1.23 → 2.62, ECE 0.18 → 0.21).
`display`/`raw` siguen solo con 14 muestras (solo PG-2337 los persiste). La
conclusión de R7.3 se refuerza: **display mejor calibrado; decision el candidato a
recalibrar** — no ahora.

## 6. Dataset readiness

| métrica | valor |
|---------|------:|
| training_ready | **false** |
| slates comparables | **3** (PG-2337, PGM-800, PG-2335) |
| partidos comparables | **37** |
| conflictos | 0 |
| con features | 37/37 |
| con rating | 33/37 |
| con money_mode | 37/37 |
| con canary | 0/37 |
| mínimo faltante | ≥8 slates (hay 3); ≥112 partidos (hay 37) |

### Sanity audit coverage (sin cambios respecto a R7.4)
- PG-2337: con sanity_audit ✓
- PGM-800: **sin** sanity_audit (ciego)
- PG-2335: **sin** sanity_audit y predicciones **sin slate_id** (ciego)

2 de 3 slates comparables son ciegas → la calibración por estado/guardrail solo es
fiable en PG-2337. Recomendación persistente: el pipeline debe guardar
`sanity_audit_json` + `slate_id` en predicciones futuras (no se backfillea aquí).

## 7. Próxima acción

Acumular más slates comparables (faltan ≥5 slates / ≥75 partidos para el umbral) y
empezar a persistir sanity-audit en predicciones nuevas. Solo entonces **proponer**
(no ejecutar) recalibración de `decision` o entrenamiento en shadow.

## 8. Qué NO cambiar todavía

- ❌ No entrenar (3 slates / 37 partidos << 8/112).
- ❌ No recalibrar `decision` aún (sin dataset suficiente).
- ❌ No bajar guardrails, no NO_SIMPLE→SIMPLE, no Money Mode más agresivo.
- ❌ No tocar canary, pricing, optimizer, thresholds.
- ❌ No backfillear sanity_audit/slate_id en esta fase.
