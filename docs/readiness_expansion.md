# Readiness Expansion (R6.3)

How to increase the matches in **READY** *without faking confidence*. This is a
**read-only audit** tool: it explains why each match is not
READY and what real data would unblock it. **It does not change states, does not lower thresholds,
does not hide LOW_EVIDENCE and does not turn evidence-less friendlies into READY.**

## Operational definition of READY

A match is **READY** when the *presentation guard* allows a defensible simple pick
(no risk/evidence blockers). `safe_to_promote_now=true` only
when the evidence is **already** sufficient — the audit never invents a READY.

## How to run the audit

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.audit_ready_expansion --draw-code PG-2338
... --draw-code PGM-801
... --active-upcoming
... --active-upcoming --json
```

Per match it reports: `position`, `match`, `current_status`, `blocked_by`,
`can_be_improved_by`, `safe_to_promote_now`. Per slate: `ready_now`,
`ready_potential_with_external_data`, `ready_potential_after_provider_results`,
`safe_promotions`, `no_promote_reason`.

## Blocker categories

`low_evidence`, `fallback_used`, `suspicious_class`, `stale_metadata`,
`friendly_context`, `placeholder_team`, `provider_unmatched`,
`canary_not_active`, `no_result_history`, `partial_rating`, `no_rating`,
`calibrator_missing`.

## What each thing unblocks (`can_be_improved_by`)

| blocker | real data that improves it |
|---|---|
| low_evidence | more result history for the teams |
| fallback_used | rating available for both teams |
| suspicious_class | better calibration |
| stale_metadata | fix metadata/mapping |
| friendly_context | friendlies-specific calibrator |
| provider_unmatched | finished result from the provider (dry-run) |
| placeholder_team | resolve the fixture's team |

## How to improve READY **without faking confidence**

Allowed (safe changes, only if there is real evidence):
- fix team/fixture **mapping**,
- fix **stale metadata**,
- use **existing ratings** when they are available for both teams,
- use the **provider dry-run** as secondary result evidence.

Forbidden:
- lowering `min_evidence` or any threshold just to see more READY,
- ignoring `fallback`/`LOW_EVIDENCE`,
- moving evidence-less friendlies to READY by default,
- removing `risk_high` or relaxing `NO SIMPLE`.

## Current state (R6.3)

For **PG-2338** and **PGM-801** (international friendlies, low evidence):

> **There are no safe READY promotions in this phase.**

Each match still has insufficient evidence, fallback or a friendly context.
The free provider does not cover these competitions, so it does not contribute
finished results as secondary evidence either. Promoting without real data
would fake confidence, so `safe_promotions = 0`. The audit makes
explicit which concrete data would unblock each match when it exists.
