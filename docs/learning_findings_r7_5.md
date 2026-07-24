# R7.5 — Apply PG-2335 Official Results (3rd Comparable Slate)

**Date:** 2026-06-24 · **Branch:** `chore/production-polish` · **Previous HEAD:** `21bf21d`
**Scope:** apply PG-2335's complete official results (user capture)
and update scoring/calibration/readiness. Apply saved; **does not train, does not
recalibrate, does not touch guardrails/Money Mode.**

---

## 1. Summary

PG-2335 moved from `closed_partial_results` (10/14) to **`closed_comparable` (14/14)**.
There are now **3 comparable slates / 37 matches**. The training gate remains
**closed** (`training_ready=false`, threshold ≥8 slates / ≥112 matches).

## 2. Apply

| field | value |
|-------|-------|
| dry-run | coverage 100% · ready_to_apply **true** · conflicts **0** · 14/14 · source high |
| checksum | `8ed62c329be3e83a50681572fb25a1c20c2362310982baad46a87fcf1206a3d9` |
| source | Manual Official Progol Results (priority 30) |
| applied positions | 1–14 (14) |
| **match_results delta** | **+14** (15172 → 15186) |

**Explanation of the +14 delta (not +4):** the manual-official route records one
official result per position (14 rows), it does not just fill the 4 gaps. The 10
previous rows (another source) **coexist and agree** with the official capture — the
dry-run confirmed `conflicts=0`, so the canonical result stays clean
(canonical prefers the manual source, priority 30). Only `match_results` changed.

The 4 positions that were missing (2 Paris SG-Arsenal=E, 3 Toluca-Tigres=E,
4 Tampico-Tepatitlán=L, 12 Avaí-Criciúma=V) now have a result; the other 10
were confirmed consistent with what was already stored.

## 3. PG-2335 scoring

**hits 6/14 (0.429)** · top1 6 · top2_cov 10/14 · brier 0.807 · logloss 4.90.

| error_type | n |
|------------|--:|
| correct | 6 |
| draw_underestimated | 4 |
| favorite_overestimated | 3 |
| away_overestimated | 1 |

Money Mode (slate NO JUGAR): 8 misses correctly blocked, 6 hits
over-blocked. `guardrail=unknown` on all 14 (PG-2335 **has no
sanity_audit** — blind predictions, see §6).

Strong error pattern: **underestimated draws** (4) — pos2 PSG-Arsenal and pos3
Toluca-Tigres ended E with p(draw)≈0.0 → very high log-loss (4.90). The model
practically excluded the draw in evenly-matched games.

## 4. Global comparable (37 matches)

| slate | hits | rate | brier | logloss |
|-------|------|-----:|------:|--------:|
| PG-2337 | 9/14 | 0.643 | 0.536 | 1.106 |
| PGM-800 | 5/9 | 0.556 | 0.689 | 1.420 |
| **PG-2335** | **6/14** | **0.429** | **0.807** | **4.900** |
| **total top-1** | **20/37 (0.541)** | | | |

PG-2335 is the worst of the three — it drags the global brier/logloss down through
draw underestimation.

## 5. Calibration update

| vector | n | brier | logloss | ECE | top1 | top2 |
|--------|--:|------:|--------:|----:|-----:|-----:|
| raw | 14 | 0.536 | 1.106 | 0.136 | 0.643 | 0.786 |
| **display** | 14 | 0.564 | 1.066 | **0.077** | 0.643 | 0.786 |
| decision | 37 | 0.676 | 2.618 | 0.212 | 0.541 | 0.757 |
| effective | 0 | — | — | — | — | — |

`decision` got worse when PG-2335 was added (logloss 1.23 → 2.62, ECE 0.18 → 0.21).
`display`/`raw` still have only 14 samples (only PG-2337 persists them). The
R7.3 conclusion is reinforced: **display better calibrated; decision the candidate to
recalibrate** — not now.

## 6. Dataset readiness

| metric | value |
|---------|------:|
| training_ready | **false** |
| comparable slates | **3** (PG-2337, PGM-800, PG-2335) |
| comparable matches | **37** |
| conflicts | 0 |
| with features | 37/37 |
| with rating | 33/37 |
| with money_mode | 37/37 |
| with canary | 0/37 |
| minimum missing | ≥8 slates (there are 3); ≥112 matches (there are 37) |

### Sanity audit coverage (unchanged from R7.4)
- PG-2337: with sanity_audit ✓
- PGM-800: **without** sanity_audit (blind)
- PG-2335: **without** sanity_audit and predictions **without slate_id** (blind)

2 of 3 comparable slates are blind → per-state/guardrail calibration is only
reliable on PG-2337. Persistent recommendation: the pipeline must save
`sanity_audit_json` + `slate_id` on future predictions (not backfilled here).

## 7. Next action

Accumulate more comparable slates (≥5 slates / ≥75 matches still needed for the threshold) and
start persisting sanity-audit on new predictions. Only then **propose**
(not execute) recalibration of `decision` or training in shadow.

## 8. What NOT to change yet

- ❌ Do not train (3 slates / 37 matches << 8/112).
- ❌ Do not recalibrate `decision` yet (no sufficient dataset).
- ❌ Do not lower guardrails, no NO_SIMPLE→SIMPLE, no more aggressive Money Mode.
- ❌ Do not touch canary, pricing, optimizer, thresholds.
- ❌ Do not backfill sanity_audit/slate_id in this phase.
