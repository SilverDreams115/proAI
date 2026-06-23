# R5.7 — Ticket / Optimizer Dry-run using Canary

R5.7 adds a **read-only, in-memory** comparison between the ticket the system
recommends today and the ticket the optimizer *would* recommend if it consumed
the team-rating canary's `effective_probabilities`. It is a dry-run only.

## What it does NOT do

- Does **not** activate the real ticket or integrate the optimizer with the canary.
- Does **not** change persisted recommendations, predictions or probabilities.
- Does **not** write any row: no `ticket_recommendation_snapshots`, no
  `match_feature_snapshots`, no `predictions`. Every path uses the pure builders
  (`TicketRecommendationService.build_read_only`,
  `PredictionService.build_slate_predictions(persist_audit=False)`,
  `FeatureService.build_match_features(persist=False)`).

## How it works

For each slate it builds two tickets in memory:

- **current** — from the served display/decision probabilities;
- **canary** — the same builder, but for canary-active positions the
  `decision_probabilities` are replaced by the canary
  `effective_decision_probabilities`. Non-canary positions are untouched.

Sanity-driven guardrails (`_allows_confident_single` / `presentation_guard`)
are preserved in **both** tickets. A match flagged *"No dejar simple / Riesgo
alto"* (e.g. **Norway vs France**) can never become a confident single in either
ticket — its primary signal (V) is shown but the recommendation stays
**NO SIMPLE / coverage**.

It then diffs the two: per-position pick type (simple/double/triple), positions
that changed, simples removed, new doubles/triples, coverage estimate and a
risk delta (`lower` / `higher` / `mixed` / `same`).

## Scope (active_upcoming)

It applies by rule to every active/upcoming slate (R5.6-D
`active_slate_scope`): currently **PG-2338** and **PGM-801**, and any future
active/upcoming slate automatically.

- **PG-2338** — has a persisted ticket; the dry-run compares it against the
  canary ticket over positions `[1,2,3,5,8,11]`.
- **PGM-801** — has no persisted ticket; the current ticket is built live from
  the read-only predictions, and the canary ticket from the canary effective
  probabilities over its eligible positions `[1,2,3,5,8]`. The UI shows
  *"sin ticket persistido · canary simulated disponible"*, never an error.

## Surfaces

- **API** (read-only):
  - `GET /api/predictions/slates/{slate_id}/ticket-canary-dry-run`
  - `GET /api/predictions/active-slates/ticket-canary-dry-run`
- **CLI** (read-only, session rolled back / `SET TRANSACTION READ ONLY`):
  - `backend/scripts/audit_ticket_canary_dry_run.py --draw-code PG-2338`
  - `... --draw-code PGM-801`
  - `... --active-upcoming`
  - `... --draw-code PG-2338 --json`
- **UI**: a *Ticket Canary Dry-run* panel in the Diagnóstico tab, badged
  **DRY-RUN · TICKET NO ACTIVO**, per selected slate.

## What is still missing for a real canary ticket

- Full activation of the team-rating gate (calibrator
  `full_activation_allowed=False`).
- A decision to let the optimizer/ticket consume canary probabilities in
  production (`ticket_integration` stays OFF).
- Validation that the canary-softened coverage improves real outcomes before any
  persisted ticket uses it.
