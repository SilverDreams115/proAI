# Team Rating Canary — Observation Report (PG-2338, R5.6-C)

- **Generated (UTC):** 2026-06-22 (R5.6-C observation & visual QA pass)
- **HEAD:** `4748f0d`
- **Slate:** `PG-2338` (`30146702-399d-40de-afff-e376b1c01396`)
- **Calibrator:** `international_friendlies_temperature_v1` · temperature `2.22` · routing `rating_replaces_fallback`
- **Competition allowlist:** ['International Friendlies']
- **Allowed positions:** [1, 2, 3, 5, 8, 11]
- **Canary active positions:** [1, 2, 3, 5, 8, 11]
- **Blocked positions:** [4, 6, 7, 9, 10, 12, 13, 14]
- **full_activation:** False · **ticket_integration:** False

This is a read-only observation of the controlled canary active locally. It
changes only the **served effective probabilities** for the active positions;
persisted predictions, picks, tickets and the DB are untouched.

## Summary

- **canary_active_count:** 6
- **blocked_count:** 8
- **top_pick_changed_count:** 0 (temperature scaling is monotonic)
- **max_probability_delta (served effective):** 0.1437 (position 11)
- **pos13 canary active:** False (partial / no rating → blocked)

> Note: the readiness/dry-run projection reports `max_probability_delta = 0.1739`
> because it scales the **persisted** prediction probabilities, whereas the canary
> scales the **served guardrailed display vector**; the served effective max delta
> is 0.1437. Both are correct for their layer; picks do not change in either.

## Per-match impact

| # | Match | Canary | display 1/X/2 | effective 1/X/2 | Δ 1/X/2 | maxΔ | pick | changed | warnings |
|---|-------|:------:|---------------|-----------------|---------|------|------|:-------:|----------|
| 1 | Czech Republic vs México | ✓ | 0.44/0.21/0.35 | 0.38/0.27/0.34 | -0.058/+0.064/-0.005 | 0.0637 | 1→1 | no | canary_active, ticket_not_using_canary |
| 2 | Switzerland vs Canada | ✓ | 0.48/0.22/0.30 | 0.40/0.28/0.32 | -0.082/+0.060/+0.022 | 0.0821 | 1→1 | no | canary_active, ticket_not_using_canary |
| 3 | Bosnia-Herzegovina vs Qatar | ✓ | 0.46/0.24/0.30 | 0.39/0.29/0.32 | -0.071/+0.050/+0.021 | 0.0710 | 1→1 | no | canary_active, ticket_not_using_canary |
| 4 | Japan vs Sweden | — | 0.60/0.20/0.20 | 0.60/0.20/0.20 | +0.000/+0.000/+0.000 | 0.0000 | 1→1 | no | — |
| 5 | Turkey vs USA | ✓ | 0.53/0.22/0.25 | 0.42/0.28/0.30 | -0.111/+0.062/+0.049 | 0.1109 | 1→1 | no | canary_active, ticket_not_using_canary |
| 6 | Paraguay vs Australia | — | 0.10/0.30/0.60 | 0.10/0.30/0.60 | +0.000/+0.000/+0.000 | 0.0000 | 2→2 | no | — |
| 7 | Norway vs France | — | 0.13/0.27/0.60 | 0.13/0.27/0.60 | +0.000/+0.000/+0.000 | 0.0000 | 2→2 | no | — |
| 8 | Cape Verde vs Saudi Arabia | ✓ | 0.45/0.25/0.30 | 0.38/0.30/0.32 | -0.066/+0.045/+0.020 | 0.0655 | 1→1 | no | canary_active, ticket_not_using_canary |
| 9 | Uruguay vs Spain | — | 0.44/0.25/0.31 | 0.44/0.25/0.31 | +0.000/+0.000/+0.000 | 0.0000 | 1→1 | no | — |
| 10 | Egypt vs Iran | — | 0.33/0.22/0.45 | 0.33/0.22/0.45 | +0.000/+0.000/+0.000 | 0.0000 | 2→2 | no | — |
| 11 | Croatia vs Ghana | ✓ | 0.60/0.28/0.12 | 0.46/0.33/0.22 | -0.144/+0.042/+0.102 | 0.1437 | 1→1 | no | canary_active, ticket_not_using_canary |
| 12 | Colombia vs Portugal | — | 0.14/0.26/0.60 | 0.14/0.26/0.60 | +0.000/+0.000/+0.000 | 0.0000 | 2→2 | no | — |
| 13 | República Del Congo vs Uzbekistan | — | 0.06/0.34/0.59 | 0.06/0.34/0.59 | +0.000/+0.000/+0.000 | 0.0000 | 2→2 | no | — |
| 14 | Algeria vs Austria | — | 0.60/0.19/0.21 | 0.60/0.19/0.21 | +0.000/+0.000/+0.000 | 0.0000 | 1→1 | no | — |

> Served effective vectors are emitted rounded to 6 decimals; one position sums
> to `0.999999` from that rounding (not a logic issue). Validated stable across
> 5 repeated `GET /predictions/slates/{id}` + `…/team-rating-canary-status` calls
> with zero DB writes.

## UI / visual QA

- **Method:** jsdom (no headless browser runner available locally — `playwright`
  is not installed; `jsdom` + `vitest` are). The real endpoint payloads
  (`team-rating-canary-status` and the slate predictions) were fed through the
  actual render helpers (`renderTeamRatingCanaryPanel`, `renderCanaryBadge`).
- **Diagnóstico → Team Rating Canary panel:** badge `CANARY ACTIVO`; allowed &
  active positions `1–3, 5, 8, 11`; blocked `4, 6–7, 9–10, 12–14`; alert
  *"Ticket recommendation not using canary yet · Full activation OFF."*
- **Match cards:** the `CANARY` badge renders only on positions `[1,2,3,5,8,11]`;
  positions `[4,6,7,9,10,12,13,14]` carry no badge. **pos13 has no CANARY badge.**
- Shadow / Activation Dry-run / Activation Readiness panels remain wired in
  `app.js` (unchanged from prior phases).

## Tests

- Backend: `855 passed` (incl. `test_team_rating_canary_service`,
  `test_prediction_canary_integration`, `test_team_rating_canary_status_endpoint`
  = 12 passed). `ruff` clean · `mypy` clean (167 files).
- Frontend: `vitest` `163 passed` (7 files, incl. `team-rating-canary.test.js`).

## Ticket / optimizer

- `ticket_integration = false` — the ticket recommendation does **not** use the
  canary; every canary-active match carries the `ticket_not_using_canary` warning.
- `ticket_recommendation_snapshots` count unchanged (162).

## Rollback

- `PROAI_TEAM_RATING_CANARY_ENABLED=false` + recreate proai → `canary_enabled=false`,
  `active_positions=[]`, every `effective_probabilities == display_probabilities`.
- Re-enabling restores `active_positions=[1,2,3,5,8,11]`. Counts identical throughout.

## Counts (before == after, worker stopped)

```
match_results=15150  predictions=2177  matches=14230  progol_slate_matches=113
match_feature_snapshots=1124  ticket_recommendation_snapshots=162
team_rating_runs=1  team_rating_snapshots=729  model_training_runs=28
```

## Conclusion

The canary behaves exactly as scoped: it recalibrates the served probabilities for
PG-2338 positions 1,2,3,5,8,11 only, leaves position 13 and all blocked positions
untouched, never flips a top pick, never touches the ticket/optimizer or the DB,
and is fully reversible by a single flag. Full activation remains OFF.
