# R7.4 — PG-2335 Official Results Intake (Comparable Slate Expansion)

**Date:** 2026-06-24 · **Branch:** `chore/production-polish` · **HEAD:** `df8b9a2`
**Scope:** attempt to turn PG-2335 into the 3rd comparable slate. Read-only except for a
saved apply (not executed). **Does not train, does not recalibrate, does not touch guardrails.**

---

## 1. PG-2335 state

| field | value |
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

Official (LN) lineage confirmed. It has 10 results already stored and complete
predictions, but **4 results are missing** → not comparable.

## 2. Comparable?

**No.** PG-2335 remains in `closed_partial_results`. 4 of 14 results are missing.

## 3. Were results applied?

**No.** There was no complete official source for the 4 missing matches in this
phase. **Final state: B — blocked with a complete manual template ready to fill.**

### Search for official results
- **Local files:** only `backend/tests/fixtures/progol_guia_2335.txt` (lineup
  guide, **not** results) and the `progol_resultados.py` connector. No
  results file.
- **Provider football_data_org:** `status=disabled`, coverage 0/14 (online query
  not allowed).
- **User capture:** not provided for PG-2335 in this phase.
- Classification: **D/E — no complete official results / source not configured.**

### Positions still to fill (4)
| pos | match | current pred |
|----:|---------|:--:|
| 2 | Paris SG vs Arsenal | L |
| 3 | Toluca vs Tigres | L |
| 4 | Tampico Madero vs Tepatitlán | L |
| 12 | Avaí vs Criciúma | E |

The other 10 positions **already have a stored result**. To apply via the
manual route (which requires 100% coverage of the file), the operator must fill all **14**
positions: the 10 existing ones with the same signs already stored (if they differ, the
`result_conflict` guard will block) and the **4 new** ones (2, 3, 4, 12) from an
official source.

### Template created
`docs/manual_results_templates/pg2335_results_template.json` — 14 positions with the
real fixture in `source_note`, `sign`/`score` as null. Rejection check:

```
provided 14/14 · coverage 100% · ready_to_apply: False
blockers: ['missing_score', 'result_conflict']
```

Rejected for missing fields (not for structure). The `result_conflict` appears
because the null signs do not match the 10 already-stored results; it disappears
once filled correctly.

## 4. PG-2335 scoring

N/A — not comparable. (Once completed and applied, scoring will be available
via `score_completed_slate --draw-code PG-2335`.)

## 5. Total comparables

**2** — PG-2337 (14) and PGM-800 (9) = **23 matches**. Unchanged from R7.3.

## 6. Calibration update

No changes (no results applied): 51 samples / 2 slates. `display` better
calibrated (ECE 0.077) than `decision` (ECE 0.18). The UI=display, decision=decision
policy is maintained.

## 7. Dataset readiness update

| metric | value |
|---------|------:|
| training_ready | **false** |
| comparable slates | 2 |
| comparable matches | 23 |
| minimum missing | ≥8 slates (there are 2); ≥112 matches (there are 23) |
| PG-2335 | excluded: `incomplete_results (10/14 canonical, 0 conflicts)` |

## 8. Sanity audit coverage

| slate | predictions | with sanity_audit (final_status) | slate_id |
|-------|--:|--:|---|
| PG-2337 | 392 | **294** | set |
| PGM-800 | 99 | **0** (blind) | set |
| PG-2335 | 756 (by match_id) | **0** (blind) | **NULL in all** |

Finding: **PGM-800 and PG-2335 have no sanity_audit** → error attribution and
per-state calibration are blind (`guardrail=unknown`). In addition, the PG-2335
predictions **do not even have `slate_id`** (they are linked only by `match_id` via the
scorer's fallback). Only PG-2337 is complete.

**Nothing was backfilled in this phase.** Recommendation to avoid future
blind predictions: the prediction pipeline must always persist `sanity_audit_json`
and link `slate_id` when generating slate predictions.

## 9. Next action

1. Obtain official results for the 4 missing PG-2335 matches
   (pos 2, 3, 4, 12) from Pronósticos/TuLotero.
2. Fill `docs/manual_results_templates/pg2335_results_template.json` (14 pos,
   the 10 existing consistent ones + the 4 new ones).
3. `validate_completed_slate_results --manual-file … --dry-run` → `ready_to_apply:true`.
4. `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS` → PG-2335 comparable (3rd slate).
5. Re-run scoring / calibration / dataset readiness.

## 10. What NOT to change yet

- ❌ Do not train (2 slates / 23 matches << threshold 8/112).
- ❌ Do not recalibrate, do not change thresholds, do not make Money Mode more aggressive.
- ❌ Do not lower guardrails or convert NO_SIMPLE into SIMPLE.
- ❌ Do not backfill sanity_audit or slate_id in this phase (that is pipeline work,
  with its own review).
- ❌ Do not touch pricing or optimizer.
