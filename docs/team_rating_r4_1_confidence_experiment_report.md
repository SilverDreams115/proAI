# Team Rating R4.1 — Confidence-Subset Experiment & Calibration Diagnostic

Status: **EXPERIMENTAL / OFFLINE. Nothing activated.** No production change, no
active model artifact, no productive calibration, no approval-gate change,
feature flag still OFF. Metrics from `backend/scripts/train_rating_experiment.py`
run read-only against the local DB (rollback; productive table counts
unchanged). Experimental boosters under `artifacts/experiments/team_rating_r4/`
(gitignored), never registered.

## 1. Executive summary

R4 left every competition at `needs_more_data` / `reject_for_now` and noted
that `rating_only` was the best-calibrated arm — i.e. the instability came from
the **baseline+rating interaction**, and the coverage gate (walk-forward
`both_medium_plus` < 0.80) blocked everything. R4.1 tests that directly by
restricting to confident-rating rows and by diagnosing calibration offline.

**Headline result:** restricted to `both_medium_plus_only` (the regime we would
actually deploy in), **International Friendlies flips to `approve_candidate`** —
rating improves Brier (−0.0267), log loss (−0.0242) and ECE (−0.0101) with all
gates passing. **Copa Libertadores reverses to `reject_for_now`** in the strict
subset (its R4 gain came from the unrated rows). **Brasileirao (control) stays
`reject_for_now`** — correct control behaviour.

**Calibration:** the XGBoost models are systematically **over-confident**. An
oracle temperature (T≈2.2–4.7, softening) cuts log loss and ECE dramatically
everywhere (e.g. Friendlies confident subset: ll 1.179→0.975, ECE 0.196→0.039).
Naive temperature fit on the train fold does the opposite (T≈0.2, sharpening →
ll explodes) because XGBoost overfits train. **Conclusion: calibration is the
real lever and it works, but only with a held-out calibration fold** — which
the productive pipeline already provides (per-league isotonic).

## 2. Why R4 did not open a gate

R4 evaluated `all_trainable` rows. There, rating's apparent gain on Libertadores
was partly the model exploiting `rating_diff=0` as a proxy for "no/weak rating"
(a coverage signal, not a strength signal), and walk-forward coverage was 0.58 —
below the 0.80 gate. R4.1 removes that confound by subsetting on confidence.

## 3. Metrics — `all_trainable` (baseline regime, reproduces R4)

| competition | rows/test | arm | top1 | top2 | Brier | log loss | ECE |
|---|---|---|---|---|---|---|---|
| Copa Libertadores | 436/131 | without | 0.435 | 0.763 | 0.767 | 1.348 | 0.275 |
| | | with_rating | 0.458 | 0.756 | **0.749** | **1.320** | 0.248 |
| | | rating_only | 0.473 | 0.817 | 0.744 | 1.289 | 0.246 |
| Intl Friendlies | 1371/411 | without | 0.513 | 0.757 | 0.626 | **1.087** | 0.125 |
| | | with_rating | 0.518 | 0.781 | 0.620 | 1.110 | 0.144 |
| Brasileirao | 1422/427 | without | 0.452 | 0.726 | **0.702** | 1.210 | **0.177** |
| | | with_rating | 0.438 | 0.766 | 0.710 | 1.206 | 0.205 |

Verdicts: Libertadores `needs_more_data` (coverage), Friendlies
`needs_more_data` (log loss + coverage), Brasileirao `reject_for_now`.

## 4. Metrics — `both_medium_plus_only` (deploy regime, coverage = 1.0)

| competition | rows/test | arm | top1 | top2 | Brier | log loss | ECE | Δbrier | Δll | Δece |
|---|---|---|---|---|---|---|---|---|---|---|
| Copa Libertadores | 254/76 | without | 0.500 | 0.737 | 0.744 | 1.444 | 0.272 | | | |
| | | **with_rating** | 0.447 | 0.763 | 0.778 | 1.481 | 0.314 | +0.035 | +0.037 | +0.043 |
| Intl Friendlies | 806/242 | without | 0.508 | 0.777 | 0.661 | 1.204 | 0.206 | | | |
| | | **with_rating** | 0.537 | 0.777 | **0.634** | **1.179** | **0.196** | **−0.027** | **−0.024** | **−0.010** |
| Brasileirao | 1315/395 | without | 0.476 | 0.757 | 0.695 | 1.209 | 0.162 | | | |
| | | **with_rating** | 0.458 | 0.782 | 0.704 | 1.198 | 0.198 | +0.009 | −0.010 | +0.036 |

Verdicts: **Friendlies `approve_candidate` (all gates passed)**; Libertadores
`reject_for_now`; Brasileirao `reject_for_now`.

## 5. Metrics — `rating_present_only` (both sides have any rating)

| competition | rows/test | with_rating Δbrier / Δll / Δece | verdict |
|---|---|---|---|
| Copa Libertadores | 385/115 | −0.025 / +0.005 / −0.055 | needs_more_data (coverage 0.66, ll) |
| Intl Friendlies | 1181/354 | +0.002 / +0.034 / +0.046 | needs_more_data |
| Brasileirao | 1395/419 | +0.008 / −0.003 / +0.045 | reject_for_now |

The `rating_present_only` cut (includes weak 1–3 match teams) is noisier than
`both_medium_plus_only` — confirming the design rule that **weak ratings should
not be treated as authoritative**.

## 6. Calibration diagnostic (offline; nothing persisted)

Temperature scaling `p_i ∝ p_i^{1/T}`, fit two ways: `train_fit` (fit on train
predictions) and `oracle_test_fit` (fit on the test fold — optimistic UPPER
BOUND on what calibration can achieve). `with_rating` arm:

| competition / subset | raw ll | oracle ll (T) | oracle ECE | train_fit ll (T) |
|---|---|---|---|---|
| Libertadores / all | 1.320 | **1.043** (T=3.68) | 0.023 | 2.320 (T=0.44) |
| Friendlies / both_mp | 1.179 | **0.975** (T=2.70) | 0.039 | 4.193 (T=0.20) |
| Brasileirao / both_mp | 1.198 | **1.046** (T=2.96) | 0.031 | 3.827 (T=0.20) |

- **Oracle softening (T>2) slashes log loss and ECE everywhere** → the boosters
  are over-confident; calibration is highly effective in principle.
- **train_fit sharpens (T<0.5) and blows log loss up** → naive calibration on an
  overfit train fold is actively harmful. A held-out calibration fold is
  mandatory before any activation.
- This is consistent with R4's observation that the raw instability was a
  calibration/interaction problem, not a lack of signal.

## 7. Diagnosis per competition

- **Copa Libertadores.** R4's promise was a coverage artifact: in the confident
  subset rating *hurts* (Brier +0.035). Test fold is thin (76). The signal is
  not robust here yet.
- **International Friendlies.** Best case: in the confident subset rating
  improves Brier, log loss AND calibration, gates pass → `approve_candidate`.
  This is the Progol-relevant competition (the bulk of slates).
- **Brasileirao (control).** Combined rating never improves it across all three
  subsets → the gate is not over-eager; do not activate.

## 8. Elo v2 design for Friendlies (DESIGN ONLY — not implemented)

Friendlies already clear the bar in the confident subset under **elo_v1**, so
elo_v2 is a *refinement*, not a prerequisite. Proposal (deferred):

- `friendly_k_multiplier = 0.5`, `official_k_multiplier = 1.0`: halve the Elo K
  for friendly fixtures so rotation-heavy friendlies move ratings less, reducing
  noise in exactly the namespace that dominates Progol.
- Implementation cost: add a per-match K multiplier keyed by competition type to
  the walk-forward update (and, for production, to `compute_team_ratings`). It is
  a **new rating methodology** → it must be a *separate* experimental run, not a
  replacement of the active `elo_v1` run, and would need its own backtest before
  any `--apply`.
- Hypothesis to test offline later: elo_v2 narrows the Friendlies log-loss gap
  further and lets the `rating_present_only` (weak-inclusive) subset also pass.

Not implemented in this phase (per scope: design only; no active-run change, no
`compute --apply`).

## 9. Recommendation

- **Copa Libertadores → reject_for_now** (was needs_more_data). Confident-subset
  evidence is negative; revisit with more seasons.
- **International Friendlies → approve_candidate (with calibration)**, gated on:
  (a) a real training run that adds the rating feature to the *full* productive
  feature set (not the proxy baseline here), (b) a held-out calibration fold
  (per-league isotonic, already in the pipeline), (c) restriction to
  `both_rating_medium_plus` matches at scoring time.
- **Brasileirao → control: do not activate.**

**No approval gate opened. Engine stays heuristic. Flag stays OFF.**

## 10. Next step

1. Re-run the ablation against the **full productive feature pipeline** (this
   experiment used a recent-form proxy baseline) for Friendlies, confident
   subset, with a held-out calibration fold.
2. If Friendlies holds under (1), open the approval gate **for Friendlies only**,
   behind `PROAI_TEAM_RATING_FEATURE_ENABLED` + a `both_medium_plus` scoring
   guard, and validate Brasileirao does not regress.
3. Optionally prototype elo_v2 (friendly K) as a separate experimental run.

## 11. Reproduce

```bash
python backend/scripts/train_rating_experiment.py \
  --competition "Copa Libertadores" \
  --competition "International Friendlies" \
  --competition "Brasileirao"   # runs all 3 subsets by default
# or a single subset:
python backend/scripts/train_rating_experiment.py --all --rating-subset both-medium-plus
```

Read-only (DB rollback). Artifacts + `metrics.json` under
`artifacts/experiments/team_rating_r4/` (gitignored).
