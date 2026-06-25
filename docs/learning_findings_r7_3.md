# R7.3 — Learning Findings Review (PG-2337 + PGM-800)

**Fecha:** 2026-06-24 · **Rama:** `chore/production-polish` · **HEAD:** `3150867`
**Alcance:** análisis read-only de lo que el sistema aprendió tras aplicar
resultados oficiales (R7.2). **No entrena, no cambia comportamiento productivo.**

---

## 1. Resumen ejecutivo

> **Decisión: NO ENTRENAR todavía.**
> **Motivo:** 2 slates / 23 partidos es muestra insuficiente (umbral ≥8 slates / ≥112 partidos).

El sistema quedó por primera vez con datos comparables reales. Aciertos top-1
globales **14/23 (60.9%)**, top-2 **78.3%**. Las decisiones de Money Mode (NO
JUGAR en ambas) **evitaron 9 fallos** pero **bloquearon 14 aciertos** — señal de
posible conservadurismo, pero NO accionable con 2 slates. Ningún fallo fue un
pick "simple" perdedor sin protección (`should_have_blocked = 0`): los guardrails
y Money Mode cubrieron el 100% de las pérdidas.

Caveat metodológico importante: las predicciones de **PGM-800 no persistieron
`sanity_audit_json`** (sin `final_status`, sin vectores raw/display), por lo que
la comparación display-vs-decision y el estado de guardrail solo son limpios
dentro de PG-2337 (14 partidos). Esto es en sí un hallazgo (ver §10).

---

## 2. PG-2337 — aciertos / fallos

**hits 9/14 (0.643)** · top1 9 · top2_cov 11/14 · brier 0.536 · logloss 1.106 · Money Mode: NO JUGAR.

| pos | partido | pred | real | hit | p(real) | error_type | guardrail |
|----:|---------|:--:|:--:|:--:|:--:|------------|-----------|
| 1 | México vs South Korea | L | L | ✓ | 0.55 | correct | ready |
| 2 | Czech Republic vs South Africa | L | E | ✗ | 0.27 | low_evidence_correctly_blocked | blocked |
| 3 | Suiza vs Bosnia | L | L | ✓ | 0.52 | guardrail_missed | blocked |
| 4 | USA vs Australia | V | L | ✗ | 0.02 | low_evidence_correctly_blocked | no_simple |
| 5 | Scotland vs Morocco | V | V | ✓ | 0.81 | guardrail_missed | no_simple |
| 6 | Turkey vs Paraguay | L | V | ✗ | 0.32 | low_evidence_correctly_blocked | blocked |
| 7 | Netherlands vs Sweden | L | L | ✓ | 0.81 | guardrail_missed | no_simple |
| 8 | Germany vs Ivory Coast | L | L | ✓ | 0.45 | correct | ready |
| 9 | Tunisia vs Japan | V | V | ✓ | 0.80 | guardrail_missed | no_simple |
| 10 | New Zealand vs Egypt | L | V | ✗ | 0.14 | low_evidence_correctly_blocked | no_simple |
| 11 | Argentina vs Austria | L | L | ✓ | 0.60 | correct | ready |
| 12 | Norway vs Senegal | V | L | ✗ | 0.03 | favorite_overestimated | ready |
| 13 | Jordania vs Algeria | V | V | ✓ | 0.79 | guardrail_missed | blocked |
| 14 | Panama vs Croatia | V | V | ✓ | 0.81 | guardrail_missed | no_simple |

Lectura: 5 fallos. 4 fueron en picks que el guardrail ya había degradado
(`low_evidence_correctly_blocked`) y 1 fue un favorito sobrestimado que SÍ estaba
READY (pos12, Norway, p=0.03 al real). 6 aciertos fueron `guardrail_missed`
(picks correctos con p(real) alto — 0.79–0.81 en cuatro — que el guardrail marcó
como no-simple).

## 3. PGM-800 — aciertos / fallos

**hits 5/9 (0.556)** · top1 5 · top2_cov 7/9 · brier 0.689 · logloss 1.420 · Money Mode: NO JUGAR.

| pos | partido | pred | real | hit | p(real) | error_type | guardrail |
|----:|---------|:--:|:--:|:--:|:--:|------------|-----------|
| 1 | México vs South Korea | L | L | ✓ | 0.55 | correct | unknown |
| 2 | France vs Senegal | L | L | ✓ | 0.45 | correct | unknown |
| 3 | England vs Croatia | V | L | ✗ | 0.03 | favorite_overestimated | unknown |
| 4 | Ghana vs Panama | L | L | ✓ | 0.52 | correct | unknown |
| 5 | Czech Republic vs South Africa | L | E | ✗ | 0.27 | draw_underestimated | unknown |
| 6 | Suiza vs Bosnia | L | L | ✓ | 0.52 | correct | unknown |
| 7 | USA vs Australia | V | L | ✗ | 0.02 | favorite_overestimated | unknown |
| 8 | Scotland vs Morocco | V | V | ✓ | 0.81 | correct | unknown |
| 9 | Turkey vs Paraguay | L | V | ✗ | 0.32 | wrong_favorite | unknown |

Lectura: 4 fallos, 2 por favorito sobrestimado con p(real) ínfimo (0.02–0.03),
1 empate subestimado, 1 favorito equivocado. `guardrail=unknown` en todos porque
estas predicciones no llevan metadata de sanity (ver §10).

Nota: PG-2337 y PGM-800 **comparten 6 fixtures** (México-Corea, Chequia-Sudáfrica,
Suiza-Bosnia, USA-Australia, Escocia-Marruecos, Turquía-Paraguay). El resultado
del partido es único por `match_id`; lo que difiere entre slates es la predicción.

---

## 4. Errores principales (global, 23 partidos)

| error_type | conteo |
|------------|-------:|
| correct | 8 |
| guardrail_missed (acierto degradado) | 6 |
| low_evidence_correctly_blocked (fallo bien bloqueado) | 4 |
| favorite_overestimated | 3 |
| draw_underestimated | 1 |
| wrong_favorite | 1 |

- **Favorito mal estimado:** 3 (`favorite_overestimated`) + 1 (`wrong_favorite`) = 4 de 9 fallos. El patrón más fuerte: el modelo asignó p≈0.02–0.03 al resultado real en 3 casos (USA-Australia x2, England-Croatia, Norway-Senegal) → sobreconfianza en el favorito que no se cumplió.
- **Empate subestimado:** 1 (Chequia-Sudáfrica, real E, p=0.27).
- **Baja evidencia:** 4 fallos correctamente bloqueados por el guardrail en PG-2337.

---

## 5. Guardrails que salvaron

- `low_evidence_correctly_blocked = 4` (PG-2337 pos 2,4,6,10): 4 fallos que el
  guardrail degradó por baja evidencia → capital protegido.
- En PGM-800 no hay metadata para acreditar guardrail (unknown), aunque varios de
  sus fallos (p≈0.02–0.03) habrían sido degradados con la misma lógica.
- **`should_have_blocked = 0`**: ningún fallo fue un pick simple sin protección.

## 6. Guardrails demasiado conservadores

- `guardrail_missed = 6` (PG-2337 pos 3,5,7,9,13,14): 6 aciertos marcados como
  no-simple. Cuatro tenían p(real) **0.79–0.81** (Scotland, Netherlands, Tunisia,
  Panama) — alta confianza que igualmente se degradó.
- Estos 6 son candidatos a revisar si pudieron ser READY sin romper evidencia,
  **pero solo cuando haya ≥8 slates.** No tocar ahora.

## 7. Money Mode review

| | conteo |
|---|---:|
| Fallos correctamente bloqueados (`money_mode_correctly_blocked`) | 9 |
| Aciertos bloqueados de más (`money_mode_too_conservative`) | 14 |

Ambas slates fueron NO JUGAR. Money Mode acertó en **evitar los 9 fallos**, pero
también bloqueó **14 aciertos top-1** (60.9% que sí pegaban). Con un boleto top-1
hipotético habrías acertado 14/23. Esto sugiere **posible exceso de
conservadurismo**, pero con 2 slates es ruido: NO es base para hacer Money Mode
más agresivo. Revisar el trade-off cuando haya ≥8 slates.

---

## 8. Calibration review

| vector | n | brier | logloss | ECE | top1 | top2 |
|--------|--:|------:|--------:|----:|-----:|-----:|
| raw_probabilities | 14 | 0.536 | 1.106 | 0.136 | 0.643 | 0.786 |
| **display_probabilities** | 14 | 0.564 | **1.066** | **0.077** | 0.643 | 0.786 |
| decision_probabilities | 23 | 0.596 | 1.229 | 0.180 | 0.609 | 0.783 |
| effective_probabilities | 0 | — | — | — | — | — |

Bucket (decision): `medium` n=3 top1 1.0 · `high` n=4 top1 0.75 · `low` n=9 top1
0.556 (ECE 0.42, mal calibrado) · `blocked` n=7 top1 0.43 (correctamente bajo).
Por estado: `ready` n=4 top1 0.75 · `revisar` n=6 top1 0.67 · `no_simple` n=10 top1 0.60.

**Conclusiones obligatorias:**
- **Mejor calibrado:** `display` (ECE 0.077, mejor logloss). *(caveat: solo 14 muestras de PG-2337).*
- **Más agresivo / peor calibrado:** `decision` (ECE 0.18) — pero es el único que cubre los 23 partidos.
- **Vector para UI:** `display` (ya en uso). Confirma la política actual.
- **Vector para decisión:** `decision` (sin cambios). Es el menos calibrado y el candidato natural a recalibración futura — **no ahora**.
- `effective` (canary) no es evaluable: no se persiste en predicciones archivadas.

---

## 9. Dataset readiness

| métrica | valor |
|---------|------:|
| training_ready | **false** |
| slates comparables | 2 (PG-2337, PGM-800) |
| partidos comparables | 23 |
| conflictos | 0 |
| con features | 23/23 |
| con rating | 20/23 |
| con money_mode | 23/23 |
| con canary | 0/23 |
| mínimo faltante | ≥8 slates (hay 2); ≥112 partidos (hay 23) |
| próxima acción | acumular más jornadas con resultados oficiales validados |

Excluidos: las demás slates por lineage no oficial o resultados incompletos
(ej. PG-2335 = 10/14, PG-2336/2338/PGM-799/801 = 0 resultados).

---

## 10. Recomendaciones técnicas

1. **Persistir `sanity_audit_json` en TODAS las predicciones de slate** (PGM-800
   no lo tiene → `guardrail=unknown`, sin vectores raw/display). Sin esto, la
   atribución de errores y la calibración por estado quedan ciegas para la mitad
   del dataset. *(requiere código, en el pipeline de predicción — futuro, no ahora.)*
2. **Acumular resultados oficiales** de más jornadas (PG-2335 ya tiene 10/14;
   completarla daría una 3ª slate comparable). *(sin código — intake manual.)*
3. **Revisar el conservadurismo de Money Mode y los `guardrail_missed`** (6
   aciertos de alta confianza degradados) — pero solo con ≥8 slates de evidencia.
4. **Mantener `display` para UI y `decision` para decisión.** La evidencia confirma
   la política; no invertirla.
5. **Candidata a recalibración futura:** `decision` (ECE 0.18). Requeriría un
   calibrador entrenado → fuera de alcance hasta tener dataset suficiente.

---

## 11. Qué NO cambiar todavía

- ❌ No activar training (2 slates / 23 partidos << umbral).
- ❌ No bajar guardrails ni convertir NO_SIMPLE en SIMPLE (los `guardrail_missed`
  son señal, no prueba, con n tan chico).
- ❌ No hacer Money Mode más agresivo (bloqueó 14 aciertos, pero evitó 9 fallos;
  insuficiente para reabrir el trade-off).
- ❌ No tocar canary, pricing, optimizer ni thresholds.
- ❌ No recalibrar `decision` aún (sin dataset).

---

## 12. Próxima acción

Acumular resultados oficiales validados hasta ≥8 slates / ≥112 partidos
comparables, persistiendo sanity-audit en todas las predicciones, y recién
entonces **proponer** (no ejecutar) un experimento de calibración/entrenamiento
en shadow para revisión manual.

---

## Hallazgos accionables

| Hallazgo | Evidencia | Riesgo | Acción recomendada | ¿Requiere código? |
|----------|-----------|--------|--------------------|:--:|
| `display` mejor calibrado que `decision` | ECE 0.077 vs 0.18 | `decision` puede estar sobreconfiado | mantener `display` en UI, `decision` en decisión | no |
| PGM-800 sin `sanity_audit_json` | guardrail=unknown en 9/9; sin raw/display | media calibración ciega | persistir sanity audit en todas las predicciones | **sí (futuro)** |
| Money Mode bloqueó 14 aciertos top-1 | mm_too_conservative=14 vs correctly_blocked=9 | posible conservadurismo | revisar con ≥8 slates | no todavía |
| 6 `guardrail_missed` con p(real) 0.79–0.81 | PG-2337 pos 5,7,9,14 | guardrail demasiado estricto | evaluar criterio READY con más datos | no todavía |
| Favorito sobreconfiado en 3 fallos | p(real)≈0.02–0.03 | sobreajuste del favorito | candidato a recalibrar `decision` | no todavía |
| top-2 coverage 78.3% | 18/23 | — | seguir usando cobertura/dobles | no |
| `should_have_blocked = 0` | ningún simple perdedor sin protección | bajo | guardrails cubren pérdidas; mantener | no |
| training_ready=false | 2 slates / 23 partidos | — | acumular resultados | no |
