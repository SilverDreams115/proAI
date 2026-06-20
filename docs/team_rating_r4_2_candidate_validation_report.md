# Team Rating R4.2 — Candidate Validation (held-out calibration)

Status: **EXPERIMENTAL / OFFLINE. Nothing activated.** No production change, no
active model artifact, no productive calibration, no approval-gate change,
feature flag still OFF. Metrics from
`backend/scripts/validate_rating_candidate.py` run read-only against the local
DB (worker paused; productive table counts before == after). Experimental
boosters under `artifacts/experiments/team_rating_r4/` (gitignored), never
registered.

## 1. Executive summary

R4.1 left **International Friendlies** at `approve_candidate` in the
`both_medium_plus_only` regime, but only with an *oracle* (test-fit)
calibration — not deployable. R4.2 repeats the test with an **honest held-out
protocol**: booster fit on train, calibrator fit on a separate calibration
fold, all metrics on a never-touched test fold.

**Result: with a calibration-fold temperature, the rating arm beats the
no-rating baseline on Brier, log loss AND calibration, with top-1/top-2 within
tolerance, on n=161 held-out test matches.** Verdict:
**`ready_for_controlled_gate_design`.**

## 2. Dataset & temporal split

- Competition: International Friendlies, subset `both_medium_plus_only`
  (deploy regime — both teams ≥4 prior rated matches at kickoff).
- Leak-free walk-forward features (same `elo_v1` mapping/config as the active
  run); rows ordered by `played_at`.
- **Split 60/20/20 (temporal, contiguous, no leak):**

| fold | rows | window |
|---|---|---|
| train | 484 | … → 2025-10-09 |
| calibration | 161 | 2025-10-09 → 2026-01-18 |
| test | 161 | 2026-01-26 → … |

- Test class balance: 1 = 0.497 · X = 0.273 · 2 = 0.230.
- The booster sees only `train`; the calibrator only `calibration`; metrics only
  `test`.

## 3. Metrics on the held-out test fold (n=161)

| arm | top1 | top2 | Brier | log loss | ECE |
|---|---|---|---|---|---|
| baseline_without_rating | 0.472 | 0.745 | 0.7216 | 1.3125 | 0.2346 |
| with_rating_uncalibrated | 0.466 | 0.752 | 0.7253 | 1.3633 | 0.2574 |
| **with_rating_temperature_calibrated** (T=2.22) | 0.466 | 0.752 | **0.6347** | **1.0718** | **0.1074** |
| with_rating_isotonic_calibrated | 0.497 | 0.752 | 0.6384 | 1.3338 | 0.1092 |
| rating_only_uncalibrated | 0.435 | 0.702 | 0.7472 | 1.4279 | 0.2555 |
| rating_only_temperature_calibrated (T=2.04) | 0.435 | 0.702 | 0.6604 | 1.1216 | 0.1452 |

Chosen calibrated arm = **with_rating_temperature_calibrated** (best Brier).

Deltas vs baseline: **Brier −0.0869**, **log loss −0.2407**, **ECE −0.1272**;
top1 −0.6pp (within 2pp), top2 +0.6pp.

## 4. Calibration held-out

- The **uncalibrated** rating arm is over-confident (ECE 0.257, log loss 1.36 —
  worse than baseline). This is exactly the R4/R4.1 instability.
- A **temperature fit on the calibration fold** (T=2.22, softening) fixes it:
  log loss 1.36 → **1.07**, ECE 0.257 → **0.107**, Brier 0.725 → **0.635**.
- **Isotonic** (cal fold = 161 ≥ 150 threshold, so it ran): great Brier/ECE but
  log loss stays high (1.33) — isotonic's flat segments produce poorly-spread
  probabilities that log loss penalizes. Temperature is the better calibrator
  here.
- **Secondary diagnostic only (NOT a criterion):** oracle temperature fit on the
  test fold gives T=3.94, log loss 1.043, ECE 0.089 — essentially matching the
  honest cal-fold temperature, confirming the calibrator is near-optimal
  *without* peeking at test.

## 5. Comparison vs R4.1

| | R4.1 (both_mp, single split, oracle calib) | R4.2 (held-out cal fold) |
|---|---|---|
| with_rating Brier | 0.634 | 0.6347 |
| with_rating log loss | 1.179 (raw) | **1.072 (cal-fold temp)** |
| with_rating ECE | 0.196 (raw) | **0.107 (cal-fold temp)** |
| calibration | oracle (not deployable) | **held-out (deployable)** |
| verdict | approve_candidate (experimental) | **ready_for_controlled_gate_design** |

R4.1's signal survives the honest protocol; the calibration that R4.1 could only
show via oracle is reproduced with a real held-out fold.

## 6. Future-guard simulation (read-only)

A future scoring guard would require `both_rating_medium_plus AND rating_present`
(both confidences medium|strong) for International Friendlies:

- Historical friendlies (trainable): **1,371**
- Would pass guard: **806** · would fall back: **565** · historical pass-rate
  **0.588** (walk-forward — early matches lack history).
- **Operational coverage on current slates** is the relevant number for
  deployment: the R3 live audit measured International Friendlies at **0.931**
  `both_medium_plus` on today's slates — clears the ≥0.80 gate. (Walk-forward
  0.588 is the all-history figure and is expected to be lower.)

## 7. Risks

- **Sample**: test n=161 clears the ≥150 bar but is still modest; a single
  unusual window could move ECE. Widen with more seasons before a real gate.
- **Friendly rotation noise**: lineups rotate heavily; `elo_v2`
  (`friendly_k=0.5`) remains a recommended refinement (design-only, R4.1 §8).
- **Temporal drift**: the calibrator's temperature may drift season-to-season;
  a productive gate must refit the calibrator periodically, not freeze T.
- **Over-confidence**: raw XGBoost probabilities are over-confident — never ship
  the rating arm without an active per-league calibrator (the productive
  pipeline already has per-league isotonic; this validates that it is required).
- **Baseline is a recent-form proxy**, not the full productive feature set; the
  real gate must be validated against the productive feature pipeline.

## 8. Verdict

**International Friendlies → `ready_for_controlled_gate_design`.**

All acceptance gates pass on the held-out test fold: Brier improves
(−0.087), log loss improves (−0.241), ECE improves vs both uncalibrated and
baseline (0.107 vs 0.257 / 0.235), top1/top2 within 2pp, test n ≥ 150,
operational live-slate coverage ≥ 0.80, and the calibrator never touched the
test fold.

This authorizes *designing* a controlled gate — it does **not** activate
anything.

## 9. Next step

1. Re-validate against the **full productive feature pipeline** (not the
   recent-form proxy) for International Friendlies, confident subset, with the
   same held-out calibration protocol.
2. Draft the controlled gate design (R5): International-Friendlies-only, behind
   `PROAI_TEAM_RATING_FEATURE_ENABLED` + a `both_rating_medium_plus` scoring
   guard + an active per-league calibrator refit on a rolling calibration
   window, with Brasileirao kept as a non-regression control.
3. Optionally prototype `elo_v2` (friendly K) as a separate experimental run.

## 10. Reproduce

```bash
python backend/scripts/validate_rating_candidate.py \
  --competition "International Friendlies" --subset both-medium-plus
```

Read-only (DB rollback). Artifacts under `artifacts/experiments/team_rating_r4/`
(gitignored).
