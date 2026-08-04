# proAI — ML Pipeline

## Production model

The production model is **XGBoost** (CPU-only). It is the only ML library allowed in the runtime. scikit-learn is explicitly excluded.

The model produces `P(home) / P(draw) / P(away)` probabilities for each match of a Progol slate. Before XGBoost, a Poisson-Dixon adjustment is applied to calibrate the draw weight according to the specific match's goal pace.

XGBoost is used only in the competitions where the published walk-forward verdict (`/data/backtest_history/index.json`, field `xgboost_beats_heuristic`) approves it; the rest routes to the **heuristic blend**: Elo + Poisson Dixon-Coles + team profile. In that blend, Elo and profile only vote the home-vs-away split and the Dixon-Coles grid owns the draw mass — mixing their full masses and renormalizing systematically diluted E (~0.30 → ~0.23), the finding from the 2026-07-16 market comparison.

### Periodic retrain of the base artifact

The worker retrains `elo_poisson_blend` when the latest run in the DB is older than `PROAI_MODEL_RETRAIN_INTERVAL_HOURS` (default 24; 0 disables it). The gate is the run's `trained_at` — not worker memory — so restarts do not re-trigger it. This keeps ratings, lambdas and calibration curves fresh without operator intervention (the May→July 2026 gap showed up as overconfidence on matches with different recent form). It is independent of the *adaptive retraining gate* below, which learns from complete Progol jornadas.

---

## High-level features

Features are built in `FeatureService` from historical results and statistics data:

- **Recent form** of home and away (window = 3 × median days between the competition's matches)
- **Head-to-head** history between the two teams
- **Goal ratios** scored and conceded per team
- **Elo ratings** derived from history
- **Competition indicators** (league, cup, qualifier)
- **Evidence count** (text-context signals — currently 0 by default because there is no active news scraper)

---

## How a prediction is generated

1. `PredictionService.predict_for_slate()` is called with the active slate.
2. For each match: `FeatureService` builds the feature vector.
3. `_has_insufficient_data()` is evaluated — if the anchors do not reach the minimum, `confidence_band = "blocked"`.
4. If the competition is in `PROAI_LIVE_PICK_BLOCKED_COMPETITIONS`, also `"blocked"`.
5. If it passes the gates: XGBoost produces raw probabilities → Poisson adjustment → confidence band.
6. The slate's `composition_hash` and `slate_version` at snapshot time are computed.
7. The result is persisted in `predictions` + `prediction_snapshots`.

### Confidence bands

```
anchored = (evidence_count >= 1) OR (h2h >= 2) OR (home_recent >= 3 AND away_recent >= 3)

"blocked"  → competition not classified, OR insufficient data (total_anchors < 4, AND
              NOT (both sides >= 2 recent OR h2h >= 3))
"high"     → top_prob >= 0.55 AND spread >= 0.12 AND anchored
"medium"   → top_prob >= 0.40 AND spread >= 0.02 AND anchored
"low"      → any other case (anchored or not, no minimum threshold)
```

Knockouts (match with no possible draw, E=0 redistribution):
```
"high"   → top_prob >= 0.55 AND anchored
"medium" → top_prob >= 0.50
"low"    → rest
```

**Non-negotiable rule:** thresholds are not relaxed to inflate bands. A match with insufficient data is shown as `low` or `blocked` — never as `medium` or `high` by an artificial rule.

---

## Anchor gap diagnostic

When a match ends up `low` for lack of anchoring, `_build_rationale()` includes a description of exactly what is missing:

- Home has N recent result(s), needs 3
- Away has N recent result(s), needs 3
- Insufficient head-to-head history (N matchup(s), needs 2)

This diagnostic is exposed in the UI and in the `/api/predictions/slates/{id}/quality` endpoint.

**Why it happens with qualifiers:** the active window is 3 × median days between matches (≈211 days for "International Friendlies"). The CONMEBOL qualifiers ended in September 2025, CAF in October 2025 — both outside the window for matches on 12 June 2026.

---

## How a ticket is generated

1. `TicketRecommendationService` receives the slate's predictions.
2. `TicketOptimizer` selects the optimal pick (1/X/2) per match based on the probabilities and the coverage target.
3. `coverage.py` (Poisson Binomial) computes P(≥K hits) given the set of picks.
4. The ticket is presented as `Simple` (14 unique picks), `Dobles` (with a second option on selected matches), and `Completa`.

---

## Scoring

After the matches are played, `JornadaScoringService` computes:

- **Hit-rate**: fraction of correct picks
- **Brier score**: squared loss of the predicted probabilities vs the real outcome

Scoring is linked to `(slate_id, composition_hash)` to guarantee it is compared against exactly the same composition that generated the prediction.

**Do not run scoring before having confirmed canonical results.** See `docs/data_quality.md`.

---

## Adaptive dataset

`AdaptiveDatasetService` assembles training rows from complete jornadas with canonical results, saved predictions, and ticket picks. Each row has:

- feature vector at prediction time
- real outcome
- ticket pick
- hit/miss

This dataset feeds the retraining gate.

---

## Adaptive retraining gate

`AdaptiveRetrainingService` evaluates readiness with these default gates:

| Gate | Default value |
|---|---|
| `min_trainable_rows` | 50 |
| `min_complete_slates` | 3 |
| `max_conflict_rate` | 5% |
| `max_blocked_rate_for_full_retrain` | 60% |
| `min_new_rows_since_last_train` | 30 |

**Mandatory flow before retraining:**
1. `GET /api/training/adaptive/readiness` — verify that all gates pass
2. `POST /api/training/adaptive/dry-run` — simulate without persisting
3. `POST /api/training/adaptive/run` — run only if the gates and the dry-run are satisfactory

**Do not run `/run` if any gate fails.** The endpoint returns 409 if readiness does not pass.

---

## Experimental neural baseline

`NeuralBaselineService` implements an MLP in pure numpy, without sklearn or torch. Design characteristics:

- `is_production = False` on every artifact it writes
- `model_type = "neural_baseline_experimental"`
- Never writes to the production prediction tables

### What the shadow actually serves

**Not the MLP.** `NeuralShadowService` calls `calibrated_proba`, which applies a
single fitted temperature to the *baseline's own* probability vector and
bypasses the MLP head entirely. Because temperature scaling is monotone per
row it provably cannot change the argmax, so the shadow can never contradict
the served pick — `top_pick_changed` is False by construction.

This is deliberate. All 13 columns in `FEATURE_NAMES` are derived from the
served baseline's output (its probabilities, its band, its own ticket pick),
so the model holds strictly *less* information than the baseline had and
cannot out-discriminate it. A free softmax head was tried and collapsed to a
single class under leave-one-slate-out across six feature subsets.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/training/neural/readiness` | Dataset readiness; never trains |
| `POST /api/training/neural/dry-run` | Trains, persists nothing |
| `POST /api/training/neural/loso` | Leave-one-slate-out; one fit per slate |
| `POST /api/training/neural/candidates/train` | Saves a non-production candidate |
| `GET /api/training/neural/candidates/latest` | Latest saved candidate |
| `POST /api/training/neural/promote` | Candidate → active (gated) |
| `GET /api/training/neural/active` | Currently promoted artifact |
| `POST /api/training/neural/rollback` | Back to the previous active run |

### Promotion gate

Promotion requires the **learning dataset gate** to pass, then out-of-sample
evidence for the vector that is actually served (`calibrated_*`, not the MLP):

- Prefers `loso`; falls back to the single-slate `holdout` when there are too
  few slates; refuses outright when neither exists.
- Requires a positive mean, a majority of folds helped, zero pick changes, and
  the worst fold at or above `LOSO_WORST_FOLD_FLOOR` (-0.10).
- Refuses any artifact whose `feature_set` is `extended` — those columns are
  not monotone in the baseline, so they could re-rank a served pick.

Unanimity across folds is deliberately **not** required. Temperature scaling
gains where the served model was overconfident and loses where it was already
sharp and right; measured over 9 folds the gain tracked the fold's realized
Brier at r=+0.99, crossing zero around 0.65. Demanding every fold improve
would reject a healthy dataset for containing a jornada the model got right.
Both the confidence band (r=+0.13 with the gain) and a per-band temperature
were tested as pre-match switches: the band carries no signal, and per-band T
was worse than global (+0.0231 vs +0.0309, 7/9 vs 8/9 folds).

**Status (2026-08-04):** 106 trainable rows over 9 complete slates; readiness
`ready`. An artifact is promoted and running as read-only shadow.

---

## What NOT to do with the ML pipeline

| Forbidden action | Reason |
|---|---|
| Relax confidence thresholds to inflate bands | Produces unfounded picks |
| Convert `low` to `medium`/`high` by an artificial rule | Violates the band semantics |
| Retrain with contaminated slates or high conflict_rate | Introduces noise into the model |
| Use conflicting results in scoring | Generates incorrect metrics |
| Train the neural baseline in production without walk-forward validation | Without evidence of improvement, it can degrade picks |
| Arbitrary legacy backfill of PG-2334/PG-2335 | See `docs/data_quality.md` |
