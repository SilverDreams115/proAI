# Team Rating — Controlled Canary Activation (R5.6-B)

R5.6-B is the first phase that changes **served** behaviour, but only for an
explicitly scoped, reversible canary. For the configured draw-code and
positions it recalibrates the **served effective probabilities** of the
prediction API with the approved temperature candidate (T=2.22) and annotates
the response. It does **not** touch the persisted prediction, the legacy
probability fields, the ticket optimizer, or any other slate / competition.

## Scope (canary)

```
scope                     = PG-2338 only
positions                 = 1,2,3,5,8,11 only (∩ what the gate would route)
competition               = International Friendlies only
routing_policy            = rating_replaces_fallback
calibrator_id             = international_friendlies_temperature_v1
temperature               = 2.22
require_both_medium_plus  = true
review_blocks             = true   (review_allowed_shadow is NOT used)
hard_blockers_block       = true
full_activation           = OFF
ticket_integration        = OFF
```

A position only goes canary-active when it is in the configured allowlist **and**
the audited dry-run says the gate would route it (both-medium-plus rating, no
review/hard sanity blocker, rating coverage, International Friendlies). So
position 13 (partial / no rating), review-blocked and hard-blocked positions are
never canary-active even if listed.

## What changes vs what stays

Additive response fields on each match (the originals are never overwritten):

```
effective_probabilities            # = temperature-scaled display vector for
effective_decision_probabilities   #   active positions; = display otherwise
canary: {
  active, engine="team_rating_canary_temperature_v1", applied,
  original_display_probabilities, probability_delta, max_abs_delta,
  original_top_pick, effective_top_pick, top_pick_changed,
  ticket_uses_canary=false, warnings=["canary_active","ticket_not_using_canary"]
}
```

`probabilities`, `display_probabilities`, `decision_probabilities`,
`raw_probabilities` and the positional `home/draw/away_probability` stay exactly
as built. Temperature scaling is monotonic, so the top pick does not flip
(`top_pick_changed=false`). The DB is never written.

## Configuration (env)

Default OFF. Enable locally with:

```
PROAI_TEAM_RATING_CANARY_ENABLED=true
PROAI_TEAM_RATING_CANARY_DRAW_CODES=PG-2338
PROAI_TEAM_RATING_CANARY_POSITIONS=1,2,3,5,8,11
PROAI_TEAM_RATING_CANARY_CALIBRATOR_ID=international_friendlies_temperature_v1
PROAI_TEAM_RATING_CANARY_ROUTING_POLICY=rating_replaces_fallback
PROAI_TEAM_RATING_CANARY_COMPETITION_ALLOWLIST=International Friendlies
```

## Endpoints / UI

- Predictions: `GET /api/predictions/slates/{slate_id}` now returns the additive
  `effective_*` / `canary` fields.
- Status: `GET /api/predictions/slates/{slate_id}/team-rating-canary-status` →
  `canary_enabled`, `scope`, `allowed_positions`, `active_positions`,
  `blocked_positions`, `full_activation=false`, `ticket_integration=false`,
  `rollback_available=true`.
- UI: Diagnóstico tab gains a **Team Rating Canary** panel (CANARY ACTIVO,
  active positions, full activation OFF, ticket not using canary). The main
  prediction cards show a `CANARY` badge only on canary-active positions.

## Rollback

Single, reversible flag flip — no DB restore needed because nothing was written:

```
1. set team_rating_gate_enabled=false        (already false)
2. set PROAI_TEAM_RATING_CANARY_ENABLED=false
3. restart proai (and worker)
4. verify predictions / match_feature_snapshots / ticket_recommendation_snapshots
   counts unchanged and the canary status shows active_positions=[]
```

A pre-activation DB dump is also kept under `backups/` as belt-and-suspenders.

## What is still OFF / pending

- Full activation (`productive_available=false`, all competitions / slates).
- Ticket optimizer / TicketRecommendationService canary integration.
- Active `PredictionService`-internal routing (the canary is an API post-process
  layer, not an internal scoring change).

See [team_rating_activation_readiness.md](team_rating_activation_readiness.md)
for the readiness gating this canary builds on.
