# Operational Runbook — Money Mode (R6.1)

Daily operation to decide **play / don't play** for each active or upcoming Progol
quiniela. The whole flow is **read-only**: no review step writes to the production
database. The system does NOT play for you — it produces an actionable decision that
you execute manually in Progol.

> **State at the R6.1 close:** PG-2338 → **NO JUGAR**, PGM-801 → **NO JUGAR**.

---

## Golden rules (non-negotiable)

1. **Never play if Money Mode says `NO JUGAR`.** It is capital protection, not a
   suggestion.
2. **Never convert a `NO SIMPLE` into a simple.** The guardrail is authoritative; if a
   position does not allow a simple, it goes with coverage (double/triple) or it does not go.
3. **Never play a slate with stale metadata** (see §"Detecting stale metadata").
4. **Never trust live predictions if `money_mode_validation` blocks** the slate
   (`data_blockers` non-empty).
5. **Never touch the production database by hand** except for a controlled, documented hotfix.

---

## Daily operation

### 1. Bring up the system
```bash
cd ~/projects/proAI
docker compose up -d proai postgres
docker compose up -d worker     # the worker does archiving/observation, it does not play
docker compose ps
```

### 2. Verify readiness
```bash
curl -s http://127.0.0.1:8000/api/ready
# expect: {"status":"ready","ready":true,"database_ok":true,"schema_up_to_date":true}
```

### 3. Review active slates
```bash
curl -s -H "Authorization: Bearer $PROAI_AUTH_API_KEY" http://127.0.0.1:8000/api/slates
```
Confirm that only active/upcoming (non-archived) slates appear and that the `match_count`
values are the expected ones.

### 4. Run the single operational command
```bash
# inside the container (the DB lives on the docker network):
docker compose exec --workdir /app/backend proai \
  python -m scripts.operate_money_mode --active-upcoming

# or, with the local venv pointing at a reachable DB:
.venv/bin/python backend/scripts/operate_money_mode.py --active-upcoming
```
Variants:
```bash
... operate_money_mode.py --draw-code PG-2338
... operate_money_mode.py --active-upcoming --json
... operate_money_mode.py --active-upcoming --markdown /tmp/money_mode_report.md
```

The report prints per slate: `SLATE`, `STATUS`, `DECISION`, `RECOMMENDED TICKET`,
`DO_NOT_SIMPLE`, `WARNINGS`, `WRITE_SAFETY`, and at the end `COUNTS_DELTA` + the
write-safety audit.

### 5. Read the final decision
- **`JUGAR …`** → proceed to step 6.
- **`NO JUGAR`** → you do not play that slate. Done for that slate (step 7).

### 6. If JUGAR — use the recommended ticket
- Use exactly the ticket marked `RECOMMENDED` (balanced by default, conservative
  if there is medium/high risk).
- Respect every `DO_NOT_SIMPLE` position: they go with coverage, never as a fixed pick.
- The ticket's combinations/cost are in the Money Mode RC detail.

### 7. If NO JUGAR — do not play that slate
- Document the reason (`reason`) and move on. Do not force a play.

### 8. Confirm counts delta zero
`operate_money_mode` itself reports `COUNTS_DELTA : ZERO`. For an independent check:
```bash
docker compose exec -T postgres psql -U proai -d proai -At -c "
SELECT 'predictions='||count(*) FROM predictions
UNION ALL SELECT 'ticket_recommendation_snapshots='||count(*) FROM ticket_recommendation_snapshots
UNION ALL SELECT 'match_feature_snapshots='||count(*) FROM match_feature_snapshots;"
```
before and after: they must be identical.

### 9. Status UI
Open the **Diagnóstico** tab → **Operational Money Mode Status** panel: it shows
JUGAR / NO JUGAR per slate, Money Mode ready, last validation and write-safety. A
`NO JUGAR` is never hidden.

---

## How to roll back the local canary

The canary is local and reversible by flag (it does not touch the real ticket). To turn it off:
```bash
# in .env
PROAI_TEAM_RATING_CANARY_ENABLED=false
docker compose up -d proai     # recreates only proai
```
To reduce the scope without turning it off, adjust `PROAI_TEAM_RATING_CANARY_POSITIONS` /
`PROAI_TEAM_RATING_CANARY_SCOPE` and recreate `proai`. The canary must never expand
beyond `active_upcoming` + gated positions.

---

## How to detect stale metadata

`money_mode_validation` (included in `operate_money_mode` and in the endpoint) reports:
- `data_blockers`: `slate_archived`, `no_matches`, `non_contiguous_positions`,
  `placeholder_teams_at_*`, `no_predictions_available`.
- `warnings`: `live_predictions_only`, `registration_closed`, `no_registration_cierre`.

Stale signals to watch for:
- `prediction_status = pending/missing` on an active slate → predictions not
  available.
- `placeholder_teams_at_*` → unresolved fixtures.
- `non_contiguous_positions` → incomplete composition.
- `registration_closed` on a slate that should be open → expired cierre or a clock
  out of sync.

If there is any `data_blocker`, the slate is **not playable**: `money_mode_ready=false` and
the decision drops to `NO JUGAR`.

---

## How to validate a new active/upcoming slate

When a new quiniela comes in, `active_slate_scope` detects it automatically (non-archived
+ future cierre). To validate it:
```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.operate_money_mode --draw-code <DRAW_CODE>
```
Quick checklist:
1. It appears in `/api/slates` as active (non-archived).
2. Correct `match_count` (typically 14 weekend / 9 midweek).
3. `prediction_status` = `persisted` or `live_available`.
4. `data_blockers` empty.
5. `operate_money_mode` produces a decision and `COUNTS_DELTA : ZERO`.

Future slates automatically inherit the whole policy (`active_upcoming`).

---

## What this flow does NOT do (by design)

no full activation · no training · no productive optimizer · no real ticket integration
· does not write tickets/predictions/feature snapshots · no results apply · no
API-Football online · does not change persisted probabilities or recommendations. Any
attempt to write the production DB or activate the real ticket is an **immediate stop**.

---

## R6.3 — Performance, external results and readiness

### Fast UI load
- The prediction dashboard loads **without waiting** for the heavy panels. Money
  Mode, the canary dry-runs, the operational status and the external results load
  **lazily** when the **Diagnóstico** tab is opened, with **per-slate cache**
  (re-opening a slate is instant) and **cancellation** of stale responses.
- Lightweight endpoint for first paint: `GET /api/operations/dashboard-fast`
  (active slates + suggestion + validation only; does not compute Money Mode).

### External results (free source)
- See `docs/free_results_provider.md`. **Resultados externos** panel in
  Diagnóstico (read-only). Probe:
  `python -m scripts.probe_free_results_source --provider football_data_org --active-upcoming`.
- Results are **never** applied automatically. Manual apply blocked
  (`scripts/apply_provider_results.py`, requires `--apply --confirm
  APPLY-PROVIDER-RESULTS-ONLY` + enablement flags; in R6.3 it responds
  NOT IMPLEMENTED).

### Readiness without faking confidence
- See `docs/readiness_expansion.md`. Audit:
  `python -m scripts.audit_ready_expansion --active-upcoming`.
- Rule: **never** promote to READY without real evidence. Current state: no
  safe promotions (low-evidence friendlies).

### Integrated operation
- `operate_money_mode.py --active-upcoming` now includes
  `readiness_expansion_summary` and `performance_note` by default (fast), and the
  provider status only with `--with-results-provider` (no network unless the
  provider is enabled).

---

## R6.4 — Per-slate options, pricing and completed-slate validation

### Ticket options (always visible)
- See `docs/progol_pricing_and_options.md`. Even if Money Mode says NO JUGAR, the
  **Opciones de boleto** panel shows aggressive/balanced/conservative/manual
  as non-recommended simulations, with combinations and cost.
- Price **not verified** by default → cost "not verified" (never $0).
- CLI: `python -m scripts.audit_slate_options --active-upcoming`.
- Pricing probe: `python -m scripts.probe_progol_pricing`.

### Completed-slate validation
- **Validación de resultados** panel + endpoints
  `GET /api/tracking/completed-slates/results-validation` and
  `/api/tracking/slates/{id}/results-validation`.
- CLI: `python -m scripts.validate_completed_slate_results --draw-code PG-2337`
  (or `--all-completed`). Read-only; reports coverage, conflicts and what is missing.

### Post-jornada backfill of official results (standard process, R7.1)

The free provider does not cover every league in the program (Sudamericana,
the Argentine/Colombian/Chilean leagues, Liga MX, MLS, Série B), and for knockout
rounds it reports scores with extra time. The canonical way to close a jornada
in the learning loop is ALWAYS the manual-official flow:

1. **Official chain**: take the contest's winning combination from
   `loterianacional.gob.mx` (Progol and Progol/Resultados list the last 15
   draws with their L/E/V sequence). That chain is the source of truth for the sign.
2. **Template**: `python -m scripts.make_manual_results_template --draw-code
   PG-XXXX` (the slate's real fixtures in `source_note`).
3. **Scores**: fill `sign` + `score` per position from verifiable sources
   (FIFA/official leagues). Progol rule: the sign counts **regular time
   (90')**; if there was extra time/penalties the `score` to capture is the 90'
   one (e.g. Belgium 3-2 Senegal in extra time ⇒ `E` / `2-2`). The provider's
   dry-run serves as a cross-check, never as the sole source.
4. **Placeholders**: if a position stayed linked to a bracket slot
   ("Winner X"), relink BEFORE applying:
   `python -m scripts.relink_slate_team --draw-code PGM-803 --position 4
   --side away --target-team "Bélgica" --dry-run` (apply with
   `--apply --confirm RELINK-SLATE-TEAM`). In-place, preserves PK/predictions.
5. **Validate and apply**: `python -m scripts.validate_completed_slate_results
   --manual-file <file> --dry-run` must return `ready_to_apply=true` and 0
   blockers; then `--apply --confirm APPLY-COMPLETED-SLATE-RESULTS`.
6. **Verify**: `GET /api/learning/dataset-readiness` must add the slate to
   `comparable_slates`.

The chain derived from the scores MUST match the official chain from step 1
100% before applying; any deviation is a sign of a mis-mapped fixture
(do not apply: investigate/relink). The automatic provider applies
(`apply_provider_results.py`, `apply_completed_slate_results.py`) remain
intentionally inert.
