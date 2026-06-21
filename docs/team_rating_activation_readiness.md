# Team Rating — Activation Readiness Hardening (R5.6-A)

R5.6-A makes the system **ready** for a minimal real activation of the
team-rating gate, **without turning it on**. It adds a read-only readiness
report (service + endpoint + CLI + UI) that lists which technical blockers
before a canary are cleared, the canary plan, the calibrator approval state and
the rollback plan.

It does **not** activate production, regenerate predictions, load a model
artifact, or change any real probability / pick / ticket / approval gate. Nothing
is written.

## `approved_inactive` calibrator state

The calibrator candidate `international_friendlies_temperature_v1` now carries
explicit readiness metadata:

```
approval_status              = "approved_inactive"
approved_for_canary          = true
active                       = false
productive_available         = false
activation_allowed_by_default = false
```

`approved_inactive` means it **passed held-out validation and is cleared for a
future canary** (R5.6-B). It does **not** mean it is active, and it does **not**
make it a productive artifact: `active` and `productive_available` stay `false`,
so the gate cannot route on it yet and the
`calibrator_productive_available_false` constraint still holds for *full*
activation.

Held-out metrics (unchanged): Brier `0.6347` vs `0.7216`, logloss `1.0718` vs
`1.3125`, ECE `0.1074` vs `0.2346`, on `161` test rows.

## Target activation (R5.6-B, still OFF)

```
scope                     = minimal_canary
competition_allowlist     = ["International Friendlies"]
routing_policy            = "rating_replaces_fallback"
calibrator_id             = "international_friendlies_temperature_v1"
temperature               = 2.22
require_both_medium_plus  = true
review_blocks             = true   (review_allowed_shadow is NOT used)
hard_blockers_block       = true
```

Hard blockers (always block): `BLOCKED`, `EXTREME_PROBABILITY_WITHOUT_EVIDENCE`,
`DATA_CONFLICT`, `PLACEHOLDER_TEAM`, `RESULT_CONFLICT`. Soft blockers the rating
can replace: `FALLBACK_USED`, `LOW_EVIDENCE`. Review blockers (block): `REVISAR`,
`review_blocked`.

## Readiness checks

| check | status (current) | meaning |
|---|---|---|
| `feature_flag_off` | `blocking_until_canary` | `team_rating_gate_enabled` must be flipped on in R5.6-B |
| `calibrator_approved_inactive` | `pass` | candidate approved for canary, still inactive |
| `calibrator_productive_available` | `blocking_until_full_activation` | a productive artifact is required for full activation, not for canary |
| `hard_sanity_blockers_present` | `block_for_affected_matches` | matches with hard blockers never route |
| `review_blockers_present` | `block_for_affected_matches` | REVISAR keeps matches on the current engine |
| `rating_coverage` | `partial_pass` | matches without full both-sides rating cannot route |
| `read_only_guards` | `pass` | readiness / dry-run / shadow paths are read-only |

`ready_for_canary` is `false` while `feature_flag_off` is blocking;
`ready_for_full_activation` is `false` while `calibrator_productive_available` is
blocking. Both flip only in later phases.

## What blocks full activation

- The gate feature flag is OFF (intentional until R5.6-B).
- No **productive** calibrator artifact yet (`productive_available = false`).
- Per-match hard/review/rating blockers keep specific matches on the current
  engine even under a canary.

## Canary plan (PG-2338)

- `canary_allowed_matches`: positions `1, 2, 3, 5, 8, 11` (would route).
- `blocked_matches`: positions `4, 6, 7, 9, 10, 12, 13, 14` (review / hard /
  rating). Position 13 is partial / no-rating.

## Rollback plan

```
1. set team_rating_gate_enabled=false
2. set team_rating_feature_enabled=false
3. restart proai and worker
4. verify predictions / match_feature_snapshots / ticket_recommendation_snapshots
   counts unchanged
```

## How to validate

### Endpoint (read-only)

```
GET /api/predictions/slates/{slate_id}/team-rating-activation-readiness
```

PG-2338 (`30146702-399d-40de-afff-e376b1c01396`): `ready_for_canary=false`,
`ready_for_full_activation=false`, `would_route=6`, `changed_top_pick_count=0`,
calibrator `approved_inactive` / `productive_available=false`, canary allowed
positions `1,2,3,5,8,11`.

### CLI (read-only, rolls back)

```
.venv/bin/python backend/scripts/audit_team_rating_activation_readiness.py --draw-code PG-2338
.venv/bin/python backend/scripts/audit_team_rating_activation_readiness.py --slate-id 30146702-399d-40de-afff-e376b1c01396 --json
.venv/bin/python backend/scripts/audit_team_rating_activation_readiness.py --competition "International Friendlies"
```

### UI

Diagnóstico tab → **Team Rating Activation Readiness** panel (below Team Rating
Shadow and the Dry-run). Shows `READINESS · NO ACTIVO`, ready-for-canary /
ready-for-full-activation, calibrator approval state, would-route / changed-pick
/ max Δ, the canary allowed/blocked positions, the readiness-checks table and the
rollback plan.

## What is still required for R5.6-B (canary)

- Flip `team_rating_gate_enabled` on behind a real, reversible flag.
- Wire the gate into `PredictionService` as an **active** route (today it is not
  integrated) preserving the approval gate and full sanity.
- Observe the canary positions only, with the rollback plan above on standby.

Until then R5.6-A stays a read-only readiness report: it activates nothing.
