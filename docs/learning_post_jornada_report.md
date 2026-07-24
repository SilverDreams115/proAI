# R7.0 — Post-jornada learning report

**Date:** 2026-06-24
**Branch:** `chore/production-polish`
**Scope:** learning loop over finished quinielas (read-only, no automatic training).

> The post-jornada learning system is **complete and operational**, but the
> actual learning is **blocked by the lack of official results** for the
> target slates. The loop will be ready to learn the moment
> validated official results are loaded.

---

## 1. State of the target slates

| Slate    | Inventory state          | Lineage                       | Predictions  | Canonical results    | Comparable | Blocker |
|----------|--------------------------|-------------------------------|--------------|----------------------|------------|---------|
| PG-2337  | `closed_pending_results` | `official_but_no_results_yet` | 14/14        | 0/14                 | ❌ No      | No official results |
| PGM-800  | `closed_pending_results` | `official_but_no_results_yet` | 9/9          | 0/9                  | ❌ No      | No official results |

Both slates have official lineage (promoted from the LN guide) and complete
predictions, but **no official result ingested yet**. They are not
comparable and do not enter the learning dataset.

The current active slates **PG-2338** and **PGM-801** remain in `NO JUGAR`
(Money Mode) and also have no results — they do not take part in learning.

---

## 2. Comparables

**Comparable slates: 0.** No slate has full canonical coverage with
official lineage. (PG-2335 has official lineage but only 10/14 results →
`closed_partial_results`, not comparable.)

---

## 3. Hits

There are no comparable hits to report: 0 comparable matches. Post-jornada
scoring exists and runs, but returns `total=0` for the target
slates because there are no official results to compare against.

---

## 4. Main errors

Not classifiable yet for PG-2337/PGM-800 (no results). The error
attribution layer (`learning_error_attribution_service`) is ready and
classifies: `wrong_favorite`, `draw_underestimated`, `favorite_overestimated`,
`away_overestimated`, `guardrail_saved`, `guardrail_missed`, `canary_*`,
`money_mode_*`, `data_quality_issue`, `result_conflict`, etc.

---

## 5. Guardrails

- **That saved us:** N/A without comparable results. The `guardrail_saved` metric
  is computed when a downgraded pick (REVISAR/BLOQUEADO) coincides with a miss.
- **That failed:** N/A without comparable results.
- **Money Mode:** PG-2338/PGM-801 in `NO JUGAR`; the correctness of that decision
  (`money_mode_correctly_blocked` vs `money_mode_too_conservative`) is only
  evaluable once results exist.

---

## 6. Calibration

`audit_learning_calibration`: **blocked** — 0 comparable samples. It measures
Brier / log-loss / ECE / top-1 / top-2 by confidence band, guardrail
state (ready / revisar / NO_SIMPLE), friendlies vs competition and by
competition, separating the `raw` / `display` / `decision` / `effective` vectors.
Does not train.

---

## 7. Dataset readiness

`audit_learning_dataset_readiness`:

- **training_ready = false**
- **Reason:** no comparable matches — no official results applied yet.
- **Missing minimums:** ≥8 comparable slates (there are 0); ≥112 comparable
  matches (there are 0).
- **Next data action:** load official results for a finished
  slate (e.g. PG-2337 / PGM-800) via the saved manual CLI, and re-run
  the audit.

---

## 8. Is training recommended?

**No.** There is neither enough comparable evidence nor official results. The
system **does not train automatically** and will not be marked `training_ready=true`
while results are missing, conflicts are high, or there are few labeled rows.

---

## 9. Next action

1. Obtain official results for PG-2337 and/or PGM-800 from a reliable source
   (TuLotero / Pronósticos / Lotería Nacional).
2. Build the safe manual file (`source: manual_official`, score per
   position) and run the dry-run:
   `python -m scripts.validate_completed_slate_results --manual-file results.json --dry-run`
3. If `ready_to_apply=true` (100% coverage, 0 conflicts, high source), apply
   with explicit confirmation:
   `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS`
4. Re-run inventory → score → attribution → calibration → dataset readiness.
5. Only then, if readiness allows, **propose** (not execute) a
   training experiment in shadow for manual review.

---

## How to run the loop (read-only)

```bash
# Inventory of finished slates
python -m scripts.learning_inventory --all

# Results validation (local + provider + manual)
python -m scripts.validate_completed_slate_results --draw-code PG-2337
python -m scripts.validate_completed_slate_results --manual-file results.json --dry-run

# Post-jornada scoring and error attribution
python -m scripts.score_completed_slate --draw-code PG-2337 --attribution
python -m scripts.score_completed_slate --all-comparable --json

# Audits
python -m scripts.audit_learning_calibration
python -m scripts.audit_learning_dataset_readiness
```

Equivalent read-only endpoints under `/api/learning/…`:
`completed-slates/inventory`, `completed-slates/scores`, `slates/{id}/score`,
`slates/{id}/attribution`, `calibration`, `dataset-readiness`.

## Economic Shadow

`score_completed_slate` and `/api/learning/completed-slates/scores` now include
`economic_shadow`: a read-only cost/break-even layer over completed slates. It
does not invent prize money. By default it reports combinations, cost units,
perfect coverage and break-even payout. ROI remains `null` until
`PROAI_ECONOMIC_SHADOW_PAYOUT_UNITS` is configured with an external payout
assumption.

## Ticket Strategy Backtest

`score_completed_slate` and `/api/learning/completed-slates/scores` also include
`ticket_strategy_backtest`: a fixed catalog of practical boleto rules such as
top-1 only, top-2 all, 4/6 uncertainty doubles, guardrail-first doubles, and
budget-capped 32/128-combination tickets. Each strategy reports covered
positions, perfect coverage, combinations, cost units, break-even payout and
optional simulated ROI. This is the ranking surface for future slates: improve
coverage without drifting into full-cover costs.
