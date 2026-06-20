# Team Rating Calibrator Candidate

`international_friendlies_temperature_v1` is a shadow-only temperature scaling
candidate for the future International Friendlies team-rating route.

## Metadata

- competition: `International Friendlies`
- subset: `both_medium_plus_only`
- routing policy: `rating_replaces_fallback`
- method: `temperature_scaling`
- temperature: `2.22`
- source experiment commit: `7bb4a9a`
- source validation commit: `857a173`
- heldout validation commit: `7bb4a9a`
- test rows: `161`
- productive_available: `false`

Held-out comparison:

| Metric | Baseline | Calibrated |
| --- | ---: | ---: |
| Brier | 0.7216 | 0.6347 |
| Logloss | 1.3125 | 1.0718 |
| ECE | 0.2346 | 0.1074 |

## Compatibility Rules

The candidate is compatible only when all are true:

- competition is `International Friendlies`
- subset is `both_medium_plus_only`
- routing policy is `rating_replaces_fallback`
- `test_rows >= 150`
- method is `temperature_scaling`
- `productive_available` remains `false`

## Shadow Usage

```bash
.venv/bin/python backend/scripts/audit_team_rating_shadow.py \
  --draw-code PG-2338 \
  --assume-gate-enabled \
  --calibrator-candidate international_friendlies_temperature_v1 \
  --assume-calibrator-candidate-available \
  --routing-policy rating-replaces-fallback
```

The auditor may report a transient calibrated probability vector for rows that
already have current predictions in memory. It never persists that vector and
does not change predictions, tickets, feature snapshots, model artifacts,
settings, migrations, or approval gates.
