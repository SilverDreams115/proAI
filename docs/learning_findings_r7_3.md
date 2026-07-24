# R7.3 — Learning Findings Review (PG-2337 + PGM-800)

**Date:** 2026-06-24 · **Branch:** `chore/production-polish` · **HEAD:** `3150867`
**Scope:** read-only analysis of what the system learned after applying
official results (R7.2). **Does not train, does not change production behavior.**

---

## 1. Executive summary

> **Decision: DO NOT TRAIN yet.**
> **Reason:** 2 slates / 23 matches is an insufficient sample (threshold ≥8 slates / ≥112 matches).

For the first time the system had real comparable data. Global top-1 hits
**14/23 (60.9%)**, top-2 **78.3%**. The Money Mode decisions (NO JUGAR on both)
**avoided 9 misses** but **blocked 14 hits** — a signal of possible
conservatism, but NOT actionable with 2 slates. No miss was an unprotected losing
"simple" pick (`should_have_blocked = 0`): the guardrails and Money Mode covered
100% of the losses.

Important methodological caveat: the **PGM-800 predictions did not persist
`sanity_audit_json`** (no `final_status`, no raw/display vectors), so the
display-vs-decision comparison and the guardrail state are only clean
within PG-2337 (14 matches). This is itself a finding (see §10).

---

## 2. PG-2337 — hits / misses

**hits 9/14 (0.643)** · top1 9 · top2_cov 11/14 · brier 0.536 · logloss 1.106 · Money Mode: NO JUGAR.

| pos | match | pred | real | hit | p(real) | error_type | guardrail |
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

Reading: 5 misses. 4 were on picks the guardrail had already downgraded
(`low_evidence_correctly_blocked`) and 1 was an overestimated favorite that WAS
READY (pos12, Norway, p=0.03 for the real outcome). 6 hits were `guardrail_missed`
(correct picks with high p(real) — 0.79–0.81 in four — that the guardrail marked
as no-simple).

## 3. PGM-800 — hits / misses

**hits 5/9 (0.556)** · top1 5 · top2_cov 7/9 · brier 0.689 · logloss 1.420 · Money Mode: NO JUGAR.

| pos | match | pred | real | hit | p(real) | error_type | guardrail |
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

Reading: 4 misses, 2 from an overestimated favorite with a tiny p(real) (0.02–0.03),
1 underestimated draw, 1 wrong favorite. `guardrail=unknown` for all because
these predictions carry no sanity metadata (see §10).

Note: PG-2337 and PGM-800 **share 6 fixtures** (México-Korea, Czechia-South Africa,
Switzerland-Bosnia, USA-Australia, Scotland-Morocco, Turkey-Paraguay). The match
result is unique per `match_id`; what differs between slates is the prediction.

---

## 4. Main errors (global, 23 matches)

| error_type | count |
|------------|-------:|
| correct | 8 |
| guardrail_missed (downgraded hit) | 6 |
| low_evidence_correctly_blocked (miss correctly blocked) | 4 |
| favorite_overestimated | 3 |
| draw_underestimated | 1 |
| wrong_favorite | 1 |

- **Mis-estimated favorite:** 3 (`favorite_overestimated`) + 1 (`wrong_favorite`) = 4 of 9 misses. The strongest pattern: the model assigned p≈0.02–0.03 to the real outcome in 3 cases (USA-Australia x2, England-Croatia, Norway-Senegal) → overconfidence in a favorite that did not deliver.
- **Underestimated draw:** 1 (Czechia-South Africa, real E, p=0.27).
- **Low evidence:** 4 misses correctly blocked by the guardrail in PG-2337.

---

## 5. Guardrails that saved us

- `low_evidence_correctly_blocked = 4` (PG-2337 pos 2,4,6,10): 4 misses the
  guardrail downgraded for low evidence → capital protected.
- In PGM-800 there is no metadata to credit the guardrail (unknown), although several of
  its misses (p≈0.02–0.03) would have been downgraded by the same logic.
- **`should_have_blocked = 0`**: no miss was an unprotected simple pick.

## 6. Guardrails that were too conservative

- `guardrail_missed = 6` (PG-2337 pos 3,5,7,9,13,14): 6 hits marked as
  no-simple. Four had p(real) **0.79–0.81** (Scotland, Netherlands, Tunisia,
  Panama) — high confidence that was downgraded anyway.
- These 6 are candidates to review for whether they could have been READY without
  breaking evidence, **but only once there are ≥8 slates.** Do not touch now.

## 7. Money Mode review

| | count |
|---|---:|
| Misses correctly blocked (`money_mode_correctly_blocked`) | 9 |
| Hits over-blocked (`money_mode_too_conservative`) | 14 |

Both slates were NO JUGAR. Money Mode got it right in **avoiding the 9 misses**, but
also blocked **14 top-1 hits** (60.9% that would have hit). With a hypothetical top-1
ticket you would have hit 14/23. This suggests **possible excess
conservatism**, but with 2 slates it is noise: it is NOT a basis to make Money Mode
more aggressive. Review the trade-off once there are ≥8 slates.

---

## 8. Calibration review

| vector | n | brier | logloss | ECE | top1 | top2 |
|--------|--:|------:|--------:|----:|-----:|-----:|
| raw_probabilities | 14 | 0.536 | 1.106 | 0.136 | 0.643 | 0.786 |
| **display_probabilities** | 14 | 0.564 | **1.066** | **0.077** | 0.643 | 0.786 |
| decision_probabilities | 23 | 0.596 | 1.229 | 0.180 | 0.609 | 0.783 |
| effective_probabilities | 0 | — | — | — | — | — |

Bucket (decision): `medium` n=3 top1 1.0 · `high` n=4 top1 0.75 · `low` n=9 top1
0.556 (ECE 0.42, poorly calibrated) · `blocked` n=7 top1 0.43 (correctly low).
By state: `ready` n=4 top1 0.75 · `revisar` n=6 top1 0.67 · `no_simple` n=10 top1 0.60.

**Mandatory conclusions:**
- **Best calibrated:** `display` (ECE 0.077, best logloss). *(caveat: only 14 samples from PG-2337).*
- **Most aggressive / worst calibrated:** `decision` (ECE 0.18) — but it is the only one covering all 23 matches.
- **Vector for the UI:** `display` (already in use). Confirms the current policy.
- **Vector for the decision:** `decision` (no change). It is the least calibrated and the natural candidate for future recalibration — **not now**.
- `effective` (canary) is not evaluable: it is not persisted in archived predictions.

---

## 9. Dataset readiness

| metric | value |
|---------|------:|
| training_ready | **false** |
| comparable slates | 2 (PG-2337, PGM-800) |
| comparable matches | 23 |
| conflicts | 0 |
| with features | 23/23 |
| with rating | 20/23 |
| with money_mode | 23/23 |
| with canary | 0/23 |
| minimum missing | ≥8 slates (there are 2); ≥112 matches (there are 23) |
| next action | accumulate more jornadas with validated official results |

Excluded: the other slates for non-official lineage or incomplete results
(e.g. PG-2335 = 10/14, PG-2336/2338/PGM-799/801 = 0 results).

---

## 10. Technical recommendations

1. **Persist `sanity_audit_json` on ALL slate predictions** (PGM-800
   does not have it → `guardrail=unknown`, no raw/display vectors). Without this,
   error attribution and per-state calibration are blind for half
   the dataset. *(requires code, in the prediction pipeline — future, not now.)*
2. **Accumulate official results** from more jornadas (PG-2335 already has 10/14;
   completing it would give a 3rd comparable slate). *(no code — manual intake.)*
3. **Review Money Mode conservatism and the `guardrail_missed`** (6
   high-confidence hits downgraded) — but only with ≥8 slates of evidence.
4. **Keep `display` for the UI and `decision` for the decision.** The evidence confirms
   the policy; do not invert it.
5. **Future recalibration candidate:** `decision` (ECE 0.18). It would require a
   trained calibrator → out of scope until there is enough dataset.

---

## 11. What NOT to change yet

- ❌ Do not enable training (2 slates / 23 matches << threshold).
- ❌ Do not lower guardrails or convert NO_SIMPLE into SIMPLE (the `guardrail_missed`
  are a signal, not proof, with n this small).
- ❌ Do not make Money Mode more aggressive (it blocked 14 hits, but avoided 9 misses;
  insufficient to reopen the trade-off).
- ❌ Do not touch canary, pricing, optimizer or thresholds.
- ❌ Do not recalibrate `decision` yet (no dataset).

---

## 12. Next action

Accumulate validated official results up to ≥8 slates / ≥112 comparable
matches, persisting sanity-audit on all predictions, and only
then **propose** (not execute) a calibration/training experiment
in shadow for manual review.

---

## Actionable findings

| Finding | Evidence | Risk | Recommended action | Requires code? |
|----------|-----------|--------|--------------------|:--:|
| `display` better calibrated than `decision` | ECE 0.077 vs 0.18 | `decision` may be overconfident | keep `display` in UI, `decision` in decision | no |
| PGM-800 without `sanity_audit_json` | guardrail=unknown in 9/9; no raw/display | calibration half-blind | persist sanity audit on all predictions | **yes (future)** |
| Money Mode blocked 14 top-1 hits | mm_too_conservative=14 vs correctly_blocked=9 | possible conservatism | review with ≥8 slates | not yet |
| 6 `guardrail_missed` with p(real) 0.79–0.81 | PG-2337 pos 5,7,9,14 | guardrail too strict | evaluate READY criteria with more data | not yet |
| Overconfident favorite in 3 misses | p(real)≈0.02–0.03 | favorite overfit | candidate to recalibrate `decision` | not yet |
| top-2 coverage 78.3% | 18/23 | — | keep using coverage/doubles | no |
| `should_have_blocked = 0` | no unprotected losing simple | low | guardrails cover losses; keep | no |
| training_ready=false | 2 slates / 23 matches | — | accumulate results | no |
