# Team Rating R4 — Offline Experiment Report

Status: **EXPERIMENTAL / OFFLINE. Nothing activated.** No production change, no
active model artifact, no approval-gate change, feature flag still OFF. All
metrics below come from `backend/scripts/train_rating_experiment.py` run
read-only against the local DB (rollback; productive table counts unchanged).
Experimental boosters live under `artifacts/experiments/team_rating_r4/`
(gitignored) and are **not** registered as an active model.

## 1. Dataset

- Source: canonical `match_results` (conflicts, sign-only and unscored
  excluded via `CanonicalResultRepository`), placeholder teams excluded —
  the exact same input mapping (`compute_team_ratings.build_input_matches`)
  the active `elo_v1` run uses. Total trainable rows: **14,073** (matches all
  arms of the active run: rated_match_count = 14,073).
- **Leak-free**: features for each match are computed walk-forward from
  matches strictly *before* it. The rating walk-forward replays the **same
  elo_v1 config** as the active run, so the final replayed ratings reconcile
  with the persisted snapshots; but the feature seen at training time is the
  *pre-match* rating, never the final one.
- Target: `1`/`X`/`2` from home/away goals.

### Feature sets (ablation)
- `without_rating` (baseline): recent-form points/goal-balance per match
  (8-match window), form gaps, head-to-head gaps, rest-gap days.
- `with_rating`: baseline + rating features (`home_rating`, `away_rating`,
  `rating_diff`, `home/away_rating_confidence`, `both_rating_medium_plus`,
  `rating_match_count_diff`).
- `rating_only`: rating features alone.

### Per-competition dataset
| competition | rows_total | rows_with_rating | both_mp_rate (walk-forward) | class balance 1/X/2 |
|---|---|---|---|---|
| Copa Libertadores | 436 | 385 | 0.583 | 0.511 / 0.243 / 0.245 |
| International Friendlies | 1,371 | 1,181 | 0.588 | 0.468 / 0.237 / 0.295 |
| Brasileirao (control) | 1,422 | 1,395 | 0.925 | 0.491 / 0.264 / 0.245 |

> **Coverage nuance.** `both_mp_rate` here is *walk-forward* coverage — the
> fraction of historical matches where **both teams already had ≥4 prior
> matches at kickoff**. It is naturally lower than the **live-slate**
> coverage reported by the R3 auditor (Libertadores 0.96, Friendlies 0.93),
> which measures today's slates against the *final* accumulated run. Early
> matches drag the walk-forward number down. The 0.80 acceptance gate is
> applied to walk-forward coverage here (conservative); for *deployment* the
> live-slate coverage is the operative number and already clears 0.80 for
> Libertadores and Friendlies.

## 2. Splits

Temporal holdout (oldest 70% train / newest 30% test, ordered by `played_at`;
no future leak — `max(train.played_at) <= min(test.played_at)`).

| competition | n_train | n_test | train_end | test_start |
|---|---|---|---|---|
| Copa Libertadores | 305 | 131 | 2025-09-26 | 2025-10-23 |
| International Friendlies | 960 | 411 | 2025-10-10 | 2025-10-10 |
| Brasileirao | 995 | 427 | 2025-11-18 | 2025-11-19 |

## 3. Metrics (test fold)

### Copa Libertadores
| arm | top1 | top2 | Brier | log loss | ECE |
|---|---|---|---|---|---|
| without_rating | 0.435 | 0.763 | 0.767 | 1.348 | 0.275 |
| with_rating | 0.458 | 0.756 | **0.749** | **1.320** | 0.248 |
| rating_only | 0.473 | 0.817 | 0.744 | 1.289 | 0.246 |

→ rating improves **Brier (−0.0185) and log loss (−0.0284)** and calibration;
only blocker is walk-forward coverage 0.583 < 0.80.

### International Friendlies
| arm | top1 | top2 | Brier | log loss | ECE |
|---|---|---|---|---|---|
| without_rating | 0.513 | 0.757 | 0.626 | **1.087** | 0.125 |
| with_rating | 0.518 | 0.781 | 0.620 | 1.110 | 0.144 |
| rating_only | 0.475 | 0.796 | 0.654 | 1.153 | 0.191 |

→ Brier slightly better (−0.0056) but **log loss worsens (+0.0225)** and
calibration (ECE) degrades; coverage 0.588 < 0.80.

### Brasileirao (control)
| arm | top1 | top2 | Brier | log loss | ECE |
|---|---|---|---|---|---|
| without_rating | 0.452 | 0.726 | **0.702** | 1.210 | **0.177** |
| with_rating | 0.438 | 0.766 | 0.710 | 1.206 | 0.205 |
| rating_only | 0.464 | 0.771 | 0.685 | 1.148 | 0.141 |

→ Combined `with_rating` does **not** improve the control (Brier +0.0082, ECE
worse). Control behaves correctly: rating does not silently "win" here.

## 4. Calibration

ECE (reliability of the predicted class). Rating helps calibration on
Libertadores (0.275→0.248), hurts slightly on Friendlies (0.125→0.144) and on
the Brasileirao combined arm (0.177→0.205). `rating_only` is consistently the
best-calibrated single signal, suggesting the *combination* with the current
baseline features (not the rating itself) is what destabilizes calibration —
a feature-interaction / regularization issue to address in a real R4+ training
(per-league isotonic calibration, lower-K-for-friendlies elo_v2), not a reason
to discard the signal.

## 5. Acceptance gates (per competition)

Gates: Brier improves AND log loss not worse AND calibration not degraded AND
walk-forward coverage ≥ 0.80 AND control (Brasileirao) not regressed.

| competition | Brier | log loss | calibration | coverage ≥0.80 | recommendation |
|---|---|---|---|---|---|
| Copa Libertadores | ✅ −0.0185 | ✅ −0.0284 | ✅ | ❌ 0.583 | **needs_more_data** |
| International Friendlies | ✅ −0.0056 | ❌ +0.0225 | ❌ | ❌ 0.588 | **needs_more_data** |
| Brasileirao (control) | ❌ +0.0082 | ✅ −0.0045 | ❌ | ✅ 0.925 | **reject_for_now** |

## 6. Risks

- **Walk-forward vs live coverage mismatch**: the 0.58 walk-forward coverage
  understates deployment readiness (live slates are 0.93–0.96). A backtest
  restricted to `both_rating_medium_plus` rows would measure the regime we'd
  actually deploy in — recommended next step.
- **Calibration interaction**: combined arm degrades ECE while `rating_only`
  improves it → needs per-league calibration before activation.
- **Friendlies log loss**: friendly rotation noise (known `should_fix`) likely
  inflates log loss; `elo_v2` lower-K-for-friendlies is the mitigation.
- **Small Libertadores test fold (131)**: promising but thin; widen with more
  seasons before approving.
- Baseline here is a **recent-form proxy**, not the full productive feature
  pipeline (no sparse evidence/injury signals). The ablation isolates the
  rating's marginal value but is not a head-to-head against the live xgboost.

## 7. Recommendation per competition

- **Copa Libertadores — needs_more_data (most promising).** Rating improves
  both Brier and log loss and calibration; only walk-forward coverage blocks.
  Next: re-run restricted to `both_medium_plus` rows + more seasons; if it
  holds, this is the first approval candidate.
- **International Friendlies — needs_more_data.** Marginal Brier gain but log
  loss + calibration regress. Requires `elo_v2` (friendly K) and per-league
  calibration before reconsidering.
- **Brasileirao (control) — reject_for_now.** Combined rating does not improve
  the control; do not activate. Confirms the gate is not over-eager.

**Overall: do NOT open any approval gate yet.** Rating shows real promise on
Libertadores under honest leak-free evaluation, but no competition clears all
gates. Engine stays heuristic; flag stays OFF.

## 8. Reproduce

```bash
python backend/scripts/train_rating_experiment.py \
  --competition "Copa Libertadores" \
  --competition "International Friendlies" \
  --competition "Brasileirao"
```

Read-only (DB rollback). Artifacts + `metrics.json` under
`artifacts/experiments/team_rating_r4/` (gitignored).
