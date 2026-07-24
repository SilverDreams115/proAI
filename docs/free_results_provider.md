# Free Results Provider (R6.3)

Read-only integration with a **free** results source so the operator can see
external scores without paying for a live feed and without ever writing the
production `match_results` table.

## Chosen provider: football-data.org

- Structured API v4, free plan, `matches` query by date/competition/status.
- **Free-plan limitations:**
  - **Delayed scores**, not real live.
  - Requires an **API key** (`X-Auth-Token`).
  - **~10 calls/min** (free-tier rate limit).
  - **Limited** competition coverage (the free plan does not cover everything; e.g.
    international/world-cup friendlies may not be present).

### Backup / cross-check
- **TheSportsDB** — backup/cross-check. Not used as primary because the
  livescores we need are on its premium plan. The probe reports it as
  `cross_check_only`.
- **Reuters World Cup page** — human/manual cross-check only, never the
  primary scraper.

## Configuration (env)

```bash
PROAI_RESULTS_PROVIDER_ENABLED=false          # default: disabled
PROAI_RESULTS_PROVIDER_PRIMARY=football_data_org
PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=true       # default: dry-run only
PROAI_FOOTBALL_DATA_API_KEY=                    # your key (NOT hardcoded)
PROAI_FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
```

Safe defaults: **disabled + dry-run-only + no writes**. With
`ENABLED=false` no network call is made.

## How to run the probe

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.probe_free_results_source --provider football_data_org
# per slate:
... --provider football_data_org --draw-code PG-2338
... --provider football_data_org --active-upcoming --json
```

The probe validates: presence of the API key, provider accessibility,
covered competitions, matches/finished found, and coverage against the
slate. **Possible states:**

| status | meaning |
|---|---|
| `ok` | provider available and with coverage |
| `disabled` | `PROAI_RESULTS_PROVIDER_ENABLED=false` (no network call) |
| `unavailable_missing_key` | `PROAI_FOOTBALL_DATA_API_KEY` missing (not fatal) |
| `insufficient_coverage` | the provider does not cover this competition/slate |
| `provider_error` | network/provider failure (not fatal) |
| `cross_check_only` | backup provider (TheSportsDB / manual) |

## Per-slate dry-run (read-only)

```bash
curl -s http://127.0.0.1:8000/api/results/slates/<id>/provider-dry-run
curl -s http://127.0.0.1:8000/api/results/active-slates/provider-dry-run
```

Output: `provider`, `enabled`, `status`, `coverage {matched,total,rate}` and per
match `{position, local_match, provider_match, status, score, confidence}`.
Always `write_safety.writes_performed = false`.

### Name matching
Reuses `NormalizationService`: resolves aliases and accents, e.g.
**México/Mexico**, **E.U.A./USA/Estados Unidos**, **Chequia/Czech Republic**.
`confidence`: `high` (both teams match), `low` (one), `none` (unmatched).

## How to interpret coverage

- `matched/total` = slate matches the provider matched with
  high confidence. For the current slates (international friendlies) the free
  plan typically gives **low or no coverage** → `insufficient_coverage`.
  It is the honest result, not an error.

## How NOT to apply results automatically

- There is **never** an automatic apply. The dry-run and the UI are read-only.
- The manual apply is **blocked by design** in this phase:

```bash
python -m scripts.apply_provider_results --draw-code PG-2338 \
    --apply --confirm APPLY-PROVIDER-RESULTS-ONLY
```

Even with the correct token, it requires `PROAI_RESULTS_PROVIDER_ENABLED=true` and
`PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=false`, and in R6.3 it responds
`NOT IMPLEMENTED` without writing anything. No results are applied in this phase.
