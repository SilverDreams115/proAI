# Team Rating — Controlled Activation Dry-run (R5.5)

R5.5 adds a **read-only, diagnostic-only** dry-run of activating the controlled
team-rating gate. It answers "if we turned the gate on under controlled rules,
what would change?" — per match: which engine, the simulated probabilities, the
simulated pick, the deltas vs the current persisted prediction, and what blocks
real activation.

It does **not** activate production. Nothing is written, no prediction is
regenerated, no model artifact is loaded, no real probability / pick / ticket /
approval-gate is touched.

## Simulated activation policy

```
competition_allowlist        = ["International Friendlies"]
routing_policy               = "rating_replaces_fallback"
calibrator_candidate         = "international_friendlies_temperature_v1"
temperature                  = 2.22
require_both_medium_plus      = true
require_calibrator_compatible = true
REVISAR blocks               = true   (review_allowed_shadow is NOT used here)
hard sanity blockers block   = true
```

- **Soft** blockers the rating can replace: `FALLBACK_USED`, `LOW_EVIDENCE`.
- **Review** blockers (block activation): `REVISAR`, `review_blocked`.
- **Hard** blockers (always block): `BLOCKED`, `EXTREME_PROBABILITY_WITHOUT_EVIDENCE`,
  `DATA_CONFLICT`, `PLACEHOLDER_TEAM`, `RESULT_CONFLICT`.

## Simulated ("dry-run") probabilities

The dry-run reuses the R5.3 calibrator candidate
(`international_friendlies_temperature_v1`), i.e. **probability-space temperature
scaling** of the *current* model probabilities (`dry_run_probability_model =
international_friendlies_temperature_v1`). Temperature scaling with `T = 2.22 > 1`
softens the distribution: it recalibrates confidence but is **monotonic**, so it
does not reorder outcomes. Consequence: the dry-run changes probabilities and can
change a (heuristic) confidence bucket, but typically **does not flip the top
pick**.

Matches that do not route keep their current probabilities (zero delta).

## What blocks real activation

The dry-run surfaces `activation_blockers` and `safe_to_activate`. With the
current repo state it is **never safe to activate**, because:

- `feature_flag_off` — `team_rating_gate_enabled = False`.
- `calibrator_productive_available_false` — the candidate is shadow-only
  (`productive_available = False`).
- `calibrator_incompatible_scope` — when the slate is not a clean
  International-Friendlies scope.
- `hard_sanity_blockers_present` — when any match carries a hard blocker.

## How to validate

### Endpoint (read-only)

```
GET /api/predictions/slates/{slate_id}/team-rating-activation-dry-run
```

PG-2338 (`30146702-399d-40de-afff-e376b1c01396`): `total_matches=14`,
`would_route=6` (positions 1,2,3,5,8,11), `blocked_by_rating=1` (pos 13, partial /
no rating), `safe_to_activate=false`.

### CLI (read-only, rolls back)

```
.venv/bin/python backend/scripts/audit_team_rating_activation_dry_run.py --draw-code PG-2338
.venv/bin/python backend/scripts/audit_team_rating_activation_dry_run.py --slate-id 30146702-399d-40de-afff-e376b1c01396 --json
.venv/bin/python backend/scripts/audit_team_rating_activation_dry_run.py --competition "International Friendlies"
```

### UI

Diagnóstico tab → **Team Rating Activation Dry-run** panel (below Team Rating
Shadow). Shows `DRY-RUN · NO ACTIVO`, would-route / would-keep-current /
changed-top-pick / max Δ, `Safe to activate: NO`, the activation blockers, and a
per-match table (current vs dry-run engine, current vs dry-run pick, Δ prob,
status, blockers).

## What is still missing for R5.6 (real controlled activation)

- A **productive** calibrator artifact (`productive_available = True`) validated
  on held-out friendlies.
- Turning `team_rating_gate_enabled` on behind a real, reversible flag.
- Wiring the gate into `PredictionService` as an **active** route (today it is
  not integrated) with the approval gate and full sanity preserved.
- Sign-off that routed matches keep correct L/E/V mapping and ticket behaviour.

Until then R5.5 stays a simulation: read-only, shadow/diagnostic only.

## R5.6-A — Activation readiness

The dry-run is consumed by the **activation-readiness** report
([team_rating_activation_readiness.md](team_rating_activation_readiness.md)),
which adds readiness checks, a canary plan, the `approved_inactive` calibrator
state and the rollback plan — still read-only, still activating nothing.
