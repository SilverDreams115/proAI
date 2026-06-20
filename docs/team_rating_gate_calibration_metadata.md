# Team Rating Gate — Calibration Metadata & Activation (R5.0)

Status: **INACTIVE BY DEFAULT. Nothing wired into production.** The controlled
gate predicate, its flags and its dry-run auditor exist, but
`team_rating_gate_enabled` is `false`, the gate is not imported by
PredictionService/FeatureService, no calibrator is loaded, and probabilities
are unchanged. This document records the validating experiment and the future
activation protocol.

## Calibrator metadata (from R4.2, commit 7bb4a9a)

Source: `backend/scripts/validate_rating_candidate.py` — held-out validation
(train 60% / calibration 20% / test 20%, temporal). Mirrored in
`backend/app/domain/team_rating_gate_config.py` (`GATE_CALIBRATOR_METADATA`).

| field | value |
|---|---|
| competition | International Friendlies |
| subset | both_medium_plus_only |
| algorithm_version | elo_v1 |
| calibration method | temperature scaling (fit on the calibration fold) |
| temperature | 2.22 |
| test_rows | 161 |
| Brier (with_rating_temp vs baseline) | 0.6347 vs 0.7216 |
| log loss (with_rating_temp vs baseline) | 1.0718 vs 1.3125 |
| ECE (with_rating_temp vs baseline) | 0.1074 vs 0.2346 |
| verdict | ready_for_controlled_gate_design |
| **productive_calibrator_available** | **false** (no weights loaded; must be refit at activation) |

This metadata loads **no calibrator weights** and registers nothing in the DB.

## Settings flags (all default OFF / conservative)

| setting | env | default |
|---|---|---|
| `team_rating_gate_enabled` | `PROAI_TEAM_RATING_GATE_ENABLED` | `false` |
| `team_rating_gate_competitions` | `PROAI_TEAM_RATING_GATE_COMPETITIONS` | `["International Friendlies"]` |
| `team_rating_gate_require_both_medium_plus` | `PROAI_TEAM_RATING_GATE_REQUIRE_BOTH_MEDIUM_PLUS` | `true` |
| `team_rating_gate_require_calibrator` | `PROAI_TEAM_RATING_GATE_REQUIRE_CALIBRATOR` | `true` |
| `team_rating_gate_min_test_rows` | `PROAI_TEAM_RATING_GATE_MIN_TEST_ROWS` | `150` |

## Gate predicate (`evaluate_team_rating_gate`)

`eligible = True` only if ALL hold:
1. `feature_flag_enabled` (i.e. `team_rating_gate_enabled`) — **short-circuits to
   `flag_disabled` when off** (current production state);
2. competition ∈ gate competitions (only International Friendlies by default);
3. `rating_present`;
4. `both_rating_medium_plus` (when `require_both_medium_plus`);
5. home AND away confidence ∈ {medium, strong};
6. `calibrator_available` (when `require_calibrator`);
7. no critical sanity blocker.

Critical sanity blockers: `LOW_EVIDENCE`, `FALLBACK_USED`, `BLOCKED`, `REVISAR`,
`EXTREME_PROBABILITY_WITHOUT_EVIDENCE`.

## Activation protocol (future, separately authorized — NOT done here)

1. Re-validate against the **full productive feature pipeline** (not the
   recent-form proxy) for International Friendlies, confident subset, with a
   held-out calibration fold; confirm Brasileirao (control) does not regress.
2. Refit and wire an **active per-league calibrator** on a rolling calibration
   window; set `GateCalibratorMetadata.productive_calibrator_available = true`
   only once a real calibrator object is available.
3. Wire `evaluate_team_rating_gate` into PredictionService **behind**
   `team_rating_gate_enabled` so the rating arm is used ONLY for eligible
   matches; everything else keeps the current engine unchanged.
4. Stage rollout: enable in a non-production environment, watch Brier / log loss
   / ECE / fallback_rate on real friendlies, then enable in production for
   International Friendlies only.
5. Keep the sanity layer authoritative; the gate never overrides a critical
   sanity blocker.

Until all of the above: gate stays OFF → engine stays heuristic → behaviour and
probabilities unchanged.
