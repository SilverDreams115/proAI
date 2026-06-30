# Money Mode Release Candidate (R6.0)

**Fecha/hora:** 2026-06-24 04:30 UTC
**HEAD (pre-commit):** `2a0a22a` — _Add ticket canary dry-run_
**Rama:** `chore/production-polish`
**Modo:** `money_mode_release_candidate` · **read-only** · full activation OFF · ticket
integration OFF · optimizer productivo OFF.

Money Mode es la capa operativa final antes de usar el sistema para decisiones de
dinero real en Progol. Para cada slate activa/próxima responde una sola pregunta —
**jugar / no jugar** — y construye **en memoria** tres boletos (agresivo, balanceado,
conservador) con su justificación por partido. No activa ni cambia el ticket real y
no escribe ninguna fila.

---

## Counts baseline (worker detenido)

| tabla | baseline | final | delta |
|---|---:|---:|---:|
| match_results | 15150 | 15150 | 0 |
| predictions | 2177 | 2177 | 0 |
| matches | 14230 | 14230 | 0 |
| progol_slate_matches | 113 | 113 | 0 |
| match_feature_snapshots | 1124 | 1124 | 0 |
| ticket_recommendation_snapshots | 162 | 162 | 0 |
| team_rating_runs | 1 | 1 | 0 |
| team_rating_snapshots | 729 | 729 | 0 |
| model_training_runs | 28 | 28 | 0 |
| progol_slates | 10 | 10 | 0 |

**Delta cero** tras construir Money Mode para ambas slates y repetir los endpoints
5× con el worker detenido. Ningún GET escribió predicciones, snapshots de feature ni
snapshots de ticket.

---

## Política Money Mode

- **Scope:** `active_upcoming`. Hoy cubre **PG-2338** (weekend) + **PGM-801** (midweek);
  cualquier slate futura entra automáticamente por la misma regla (`active_slate_scope`).
- **Canary:** en posiciones canary-activas se consumen las
  `effective_decision_probabilities`; el resto usa la vista decisión/display actual.
- **Guardrail (autoritativo):** una posición con `presentation_guard.simple_allowed=false`
  **nunca** aparece como simple. Un fijo forzado sobre una posición NO SIMPLE se reporta
  como `no_simple` (riesgo descubierto), de modo que un partido `no_dejar_simple` /
  `risk_high` / `review` / `blocked` jamás se lee como fijo.
- **Boletos = modos del optimizer** (respetan las reglas de boleto Progol):
  - **agresivo** = modo `simple` (más fijos, más barato).
  - **balanceado** = modo `doubles` (plan acotado de dobles, recomendado por defecto).
  - **conservador** = modo `full` (máxima cobertura dobles+triples permitida).
- **Costo:** no existe tarifa por combinación configurada en el sistema →
  `estimated_cost = null` y se documenta. Se reporta `estimated_combinations`
  (producto de 1 simple · 2 doble · 3 triple).
- **Regla de decisión:** si incluso el boleto **conservador** deja más del 34 % de las
  posiciones NO SIMPLE como fijo forzado → **NO JUGAR** (riesgo no cubrible). Si el
  balanceado cubre todos los NO SIMPLE → JUGAR BALANCEADO. Casos intermedios →
  JUGAR SOLO CONSERVADOR.

---

## PG-2338 · weekend · 14 partidos

- **DECISIÓN: `NO JUGAR`** (confianza: cautious) — boleto recomendado: **ninguno**.
- **Motivo:** demasiados NO SIMPLE sin cobertura posible: **6/14** posiciones siguen
  como fijo forzado incluso en el boleto conservador (máxima cobertura permitida por
  las reglas del boleto). El riesgo no es cubrible y ningún modo alcanza el target de
  cobertura.
- **Predicciones:** persistidas. Sin data blockers.

| boleto | S / NS / D / T | combinaciones | costo | E[aciertos] | jackpot | riesgo | cubre NO SIMPLE |
|---|---|---:|---|---:|---:|---|---|
| agresivo | 0 / 14 / 0 / 0 | 1 | n/d | 6.91 | 0.0000 | very_high | no (14 desc.) |
| balanceado | 0 / 6 / 8 / 0 | 256 | n/d | 9.49 | 0.0040 | very_high | no (4,6,7,12,13,14) |
| conservador | 0 / 6 / 4 / 4 | 1296 | n/d | 10.63 | 0.0153 | very_high | no (4,6,7,12,13,14) |

- **Partidos NO SIMPLE:** **todos (1–14)** — son amistosos internacionales con
  evidencia baja / clase sospechosa / bloqueados.
- **Revisión obligatoria:** 1,3,4,5,6,7,8,9,10,11,12,13,14.
- **Canary influye en:** 1,2,3,5,8,11.
- **Riesgo principal:** ni el boleto de máxima cobertura cubre el riesgo; 6 partidos
  quedan como volado forzado sobre el pick más probable y el target de cobertura no se
  cumple en ningún modo.

## PGM-801 · midweek · 9 partidos (predicción live)

- **DECISIÓN: `NO JUGAR`** (confianza: cautious) — boleto recomendado: **ninguno**.
- **Motivo:** demasiados NO SIMPLE sin cobertura posible: **4/9** posiciones siguen
  como fijo forzado incluso en el boleto conservador.
- **Predicciones:** live (sin ticket persistido) — Money Mode calculado en vivo.
  Warning: `live_predictions_only`. Sin data blockers.

| boleto | S / NS / D / T | combinaciones | costo | E[aciertos] | jackpot | riesgo | cubre NO SIMPLE |
|---|---|---:|---|---:|---:|---|---|
| agresivo | 0 / 9 / 0 / 0 | 1 | n/d | 4.11 | 0.0007 | very_high | no (1–9 desc.) |
| balanceado | 0 / 6 / 3 / 0 | 8 | n/d | 5.12 | 0.0048 | very_high | no (2,4,5,6,7,9) |
| conservador | 0 / 4 / 3 / 2 | 72 | n/d | 6.34 | 0.0296 | very_high | no (4,5,6,7) |

- **Partidos NO SIMPLE:** **todos (1–9)**.
- **Revisión obligatoria:** 1,4,5,7,8,9.
- **Canary influye en:** 1,2,3,5,8.
- **Riesgo principal:** mismo patrón que PG-2338 — slate de amistosos de baja
  evidencia; aun con la cobertura máxima quedan 4 volados forzados.

---

## Active-upcoming summary

- **2** slates en scope (`active_upcoming`): PG-2338 + PGM-801.
- **0** slates jugables (`playable_slate_count = 0`): ambas → **NO JUGAR**.
- Futuras slates heredan la política automáticamente.

## Norway vs France (caso guardrail)

- PG-2338 pos 7 / PGM-801 pos 6: **NO SIMPLE** en ambas. El `money_mode_pick` es
  cobertura (doble V/E en el plan conservador), nunca un fijo. Motivos:
  `risk_high`, `no_dejar_simple`, `suspicious_class`. El guardrail se respeta en los
  tres boletos y en la UI.

---

## Validación de no-escritura

- `write_safety = { writes_performed: false, snapshots_created: false }` en todas las
  respuestas (servicio, endpoint, CLI).
- Transacción marcada `SET TRANSACTION READ ONLY` y siempre con rollback (helper
  `read_only_transaction`).
- Endpoints repetidos 5× con worker detenido → **counts delta cero** (tabla arriba).
- Ticket real de PG-2338 intacto (snapshots = 162, sin cambios).

## Conclusión

> **PG-2338 → NO JUGAR. PGM-801 → NO JUGAR.**

El sistema produce salida accionable y la decisión honesta es **no jugar ninguna de
las dos slates**: son jornadas de amistosos internacionales con evidencia baja donde
todas las posiciones están marcadas NO SIMPLE, y ni el boleto de máxima cobertura
permitido por las reglas de Progol cubre el riesgo (6/14 y 4/9 volados forzados,
target de cobertura no alcanzado). Money Mode protege el dinero: no convierte ninguna
señal peligrosa en simple y no recomienda un boleto que no cubre el riesgo principal.

---

## Restricciones respetadas

no full activation · no training · no optimizer productivo · no ticket integration
real · no ticket snapshot writes · no prediction writes · no match_feature_snapshot
writes · no cambios a recommendations persistidas · no results apply · no API-Football
online · no schema changes / migrations · no deletes/reverts · no push/remote sin
autorización · no secrets · canary sin ampliar fuera de `active_upcoming` · guardrail
NO SIMPLE respetado · ticket real intacto.
