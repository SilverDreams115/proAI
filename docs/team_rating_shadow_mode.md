# Team Rating Shadow Mode

R5.1 shadow mode is a read-only audit layer for the inactive team-rating gate.
It answers what would have happened if the controlled International Friendlies
gate were enabled, while production still runs exactly as before.

## What It Does Not Do

- It does not enable `PROAI_TEAM_RATING_FEATURE_ENABLED`.
- It does not enable `PROAI_TEAM_RATING_GATE_ENABLED`.
- It does not regenerate predictions or probabilities.
- It does not write `predictions`, `match_feature_snapshots`, or
  `ticket_recommendation_snapshots`.
- It does not train, calibrate, optimize, fetch online data, apply results, or
  create model artifacts.
- It does not import the shadow service from `PredictionService`,
  `FeatureService`, or `TicketRecommendationService`.

## How To Audit

Use the read-only script:

```bash
.venv/bin/python backend/scripts/audit_team_rating_shadow.py --draw-code PG-2338

.venv/bin/python backend/scripts/audit_team_rating_shadow.py \
  --draw-code PG-2338 \
  --assume-gate-enabled \
  --assume-calibrator-available

.venv/bin/python backend/scripts/audit_team_rating_shadow.py \
  --competition "International Friendlies" \
  --assume-gate-enabled \
  --assume-calibrator-available \
  --routing-policy rating-replaces-fallback
```

The script opens one DB session, reads the active `team_rating_runs` row, rating
snapshots, slate matches, latest prediction sanity audit JSON, and then rolls
the session back.

## Interpreting Blockers

- `flag_disabled`: the productive gate flag is OFF, so current behavior cannot
  route to rating.
- `competition_not_allowed`: the match competition is outside the gate allowlist.
- `rating_not_present`: at least one side has no usable rating snapshot.
- `not_both_medium_plus`: both sides do not meet the medium-plus rating sample
  threshold.
- `home_confidence_too_low` / `away_confidence_too_low`: a side is below the
  accepted confidence buckets.
- `calibrator_unavailable`: the gate requires a productive calibrator and the
  audit did not simulate one.
- `sanity_blocked`: existing sanity audit flags would still keep the match on
  fallback even if the rating guard passed.

`eligible_if_enabled` is the shadow rating guard view: gate ON, optional
calibrator assumption, and no legacy sanity flags. `would_use_rating_model` is
the stricter view that also applies current sanity blockers.

## Routing Policy

R5.2 adds a shadow-only routing policy so the audit can separate true blockers
from fallback-era artifacts. The policy is selected with `--routing-policy` and
is never imported by production services.

- `strict`: treats `FALLBACK_USED`, `LOW_EVIDENCE`, and `REVISAR` as blockers.
  This is the conservative R5.1-compatible view.
- `rating-replaces-fallback`: allows `FALLBACK_USED` when the rating gate
  passes, and allows `LOW_EVIDENCE` when both teams are medium-plus and a
  calibrator is available. `REVISAR` still blocks.
- `review-allowed-shadow`: same as `rating-replaces-fallback`, but `REVISAR`
  becomes a warning instead of a blocker.

Hard sanity blockers always block in every policy:

- `BLOCKED`
- `EXTREME_PROBABILITY_WITHOUT_EVIDENCE`
- `DATA_CONFLICT`
- `PLACEHOLDER_TEAM`
- `RESULT_CONFLICT`

Policy warnings are audit output only. They do not change predictions, feature
snapshots, tickets, approval gates, settings, or model artifacts.

## Calibrator Candidate

R5.3 adds one commitable calibrator candidate for shadow audits:

- id: `international_friendlies_temperature_v1`
- competition: `International Friendlies`
- subset: `both_medium_plus_only`
- method: `temperature_scaling`
- temperature: `2.22`
- routing policy: `rating_replaces_fallback`
- test rows: `161`
- held-out metrics: Brier `0.6347` vs baseline `0.7216`, logloss `1.0718`
  vs baseline `1.3125`, ECE `0.1074` vs baseline `0.2346`
- productive_available: `false`

Audit it with:

```bash
.venv/bin/python backend/scripts/audit_team_rating_shadow.py \
  --competition "International Friendlies" \
  --assume-gate-enabled \
  --calibrator-candidate international_friendlies_temperature_v1 \
  --assume-calibrator-candidate-available \
  --routing-policy rating-replaces-fallback
```

The auditor validates competition, subset, routing policy, and minimum test
rows before treating the candidate as available. A compatible candidate only
changes the shadow report. It does not enable the production gate, register an
artifact, write to the database, or alter any persisted probability.

## Before Real Activation

- Refit and register a productive calibrator instead of relying on experiment
  metadata.
- Run a fresh held-out backtest for the intended activation window.
- Define rollback criteria and observability for live traffic.
- Add an explicit production integration PR for `FeatureService` /
  `PredictionService`; shadow mode intentionally does not wire either path.
- Reconfirm counts and feature flags before and after any activation rehearsal.

## Pending Risks

- The current sanity audit JSON comes from fallback-era predictions and can
  conservatively block otherwise rating-eligible matches.
- Rating coverage can drift as slates change; the shadow report must be rerun
  close to activation time.
- Non-International-Friendlies competitions remain audit controls unless the
  gate allowlist is changed in a separate activation review.

## R5.5 — Controlled Activation Dry-run

The shadow projection is extended by a **controlled-activation dry-run** that
also simulates the resulting probabilities / picks per match and reports
`safe_to_activate` plus `activation_blockers`. It is still read-only and
diagnostic only — it does not activate the gate, regenerate predictions, or
change real probabilities / picks / tickets. See
[team_rating_activation_dry_run.md](team_rating_activation_dry_run.md) for the
policy, the simulated probability model, the endpoint/CLI/UI, and what is still
required for R5.6 (real controlled activation).
