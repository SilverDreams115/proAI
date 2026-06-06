# Sprint 7.1 — Expected Goals model evaluation

## Verdict: **DO NOT INTEGRATE** (insufficient signal vs the league-mean baseline).

## Setup

- Corpus: 14082 resulted matches from the production DB (2026-05-29).
- Split: chronological 70/30 (no shuffling). 9118 train, 3908 test.
- Model: XGBoost `reg:squarederror`, max_depth=4, learning_rate=0.05,
  240 rounds. Features pinned by [expected_goals_features.FEATURE_NAMES]
  (rolling goals_for/against 5+10, points_per_match_10, home_indicator,
  competition baselines, days_rest).
- Baseline: predicting the per-competition long-run mean goals for each
  side.

## Headline numbers

| Metric | xG model | League-mean baseline | Δ |
| --- | --- | --- | --- |
| RMSE | **1.2130** | 1.2109 | +0.0021 (worse) |
| MAE | **0.9518** | 0.9547 | -0.0029 (better) |

Reading: the booster cuts MAE by ~0.3% and *adds* RMSE by ~0.2%. Net
signal is in the noise.

## Per-competition breakdown (test samples ≥ 10)

The booster ties or loses against the baseline on every league with
enough samples. No competition shows a meaningful win.

Notable losses:

| Competition | Test rows | xG RMSE | Baseline RMSE | Δ |
| --- | --- | --- | --- | --- |
| MLS | 652 | 1.3564 | 1.3275 | +0.029 |
| International Friendlies | 316 | 1.4627 | 1.4361 | +0.027 |
| Copa Libertadores | 258 | 1.3349 | 1.318 | +0.017 |
| Liga de Expansion MX | 354 | 1.2679 | 1.252 | +0.016 |

## Why this was expected

With only final-score data, the irreducible Poisson variance dominates
the squared loss. Real xG models need shot-level events (xG per shot →
sum to xG per match) because the actual goals scored is a noisy draw
from the underlying scoring rate. We are predicting the noisy outcome,
not the rate — so a smarter model can't out-perform the long-run mean
by much.

## Why we still kept the model

- `ExpectedGoalsService` is built, tested, and persisted via
  `artifact_storage`. The plumbing exists for the day we add shot-level
  data.
- The training data shows the booster *does* learn the home indicator
  and rolling form (sanity-check tests in
  `tests/test_expected_goals_service.py` confirm the booster beats the
  baseline on the synthetic dataset where signal is artificially
  strong).
- The CLI command `proai evaluate-xg --train-fraction 0.7` is now part
  of the operational toolkit — re-running it after any future data
  improvement is one command.

## What would actually unlock xG

Either of the following would justify revisiting the integration:

1. **Shot-level ingestion.** A connector for StatsBomb open data (covers
   ~7 of the leagues we already track) or Understat scraping would let
   us train a per-shot model and sum to a true xG-per-team. This is a
   data-source project, not a modelling project.
2. **Richer match-level features.** Possession share, shots on target,
   corners, and dangerous attacks all live in match metadata that some
   feeds expose. Adding even three of those — *if* TSDB or
   football-data.org returned them — would likely push the booster
   above the baseline.

Without either input, training a heavier model is wasted compute.

## Action items

- [ ] **Closed: integration.** `_competition_lambda_priors` keeps its
  current heuristic.
- [ ] **Future: shot-level data audit.** When a connector for shot
  events appears, re-run `proai evaluate-xg`. If RMSE drops by ≥0.05 on
  the top-5 covered competitions, plan the integration sprint.
- [ ] **Keep watching.** The `ExpectedGoalsService` import has zero
  runtime cost today; leaving it in keeps the future migration cheap.
