# proAI SLIs, SLOs, and Runbook

This is the operational contract for the single-tenant proAI deployment.
The system runs on one box behind one user; the SLOs reflect that scope
— they're tight enough to catch real degradation but loose enough that a
brief restart or a slow ingest tick is not a page.

If a number below is wrong, fix it here first, then change the alert that
references it. Don't add code that silently changes a threshold.

## Service Level Indicators

| SLI | What it measures | Where to read it |
| --- | --- | --- |
| `availability` | Percentage of `/api/ready` probes returning 2xx over a 24h window | Docker healthcheck history + uptime via `proai.last_started` |
| `request_latency_p95` | 95th-percentile end-to-end latency for `/api/predictions/slates/*` | `metrics_store` histogram (Prometheus scrape) |
| `prediction_freshness` | Seconds since the last `predictions.generated_at` for the active slate | `/api/health` payload (`operational_signals.verdict_age_seconds`) |
| `ingest_freshness` | Seconds since the last successful source ingest | `/api/health` payload (`operational_signals.last_ingest_age_seconds`) |
| `worker_health` | Worker reports `healthy` and last cycle ran within the configured interval | `/api/health` payload (`operational_signals.worker_state`) |

## Service Level Objectives

| SLO | Target | Window | Notes |
| --- | --- | --- | --- |
| Availability | **99.0%** | 30-day rolling | One full restart per week eats ~0.1%; tolerate up to ~7h/month of downtime |
| Prediction request latency | **p95 ≤ 800ms** | 1h rolling | Includes feature lookup + isotonic curves; warm cache hits should be <50ms |
| Prediction freshness | **verdict_age ≤ 24h** | continuous | Stale beyond 24h means the auto-promote worker is broken |
| Ingest freshness | **last_ingest_age ≤ 36h** | continuous | TSDB cycles daily; football-data.org runs hourly; gap of 36h is the conservative floor |
| Test pass rate | **268/268** | per CI run | Hard gate — any flake gets investigated, not retried |
| Coverage on pure modules | **≥85%** | per CI run | `model_training_math.py`, `model_training_metrics.py`, `ratelimit.py`, `helpers.js`. Lower files are tracked but not gated. |

### Why these numbers

- **99.0%** instead of three or four nines: one-box deployment with no
  failover. A power cycle, a kernel update, or a Docker daemon restart
  costs minutes; manufacturing tighter availability targets would just
  push toward false reds.
- **Prediction freshness 24h**: the Progol contest cycle is weekly. As
  long as we re-rank within a day of any new evidence, the boleta stays
  honest.
- **Ingest freshness 36h**: TSDB has occasional 12h gaps for less-active
  leagues; 36h catches a stuck connector without paging on a normal lull.

## Runbook

### Symptom: `verdict_age_seconds` > 86400

Means: no new prediction in the last 24 hours.

1. `curl -s http://localhost:8000/api/health | jq .operational_signals`
   to confirm the field.
2. Check the worker: `docker logs proai-worker --tail 200 | grep -i error`.
3. If the worker is healthy, force a refresh:
   `curl -X POST -H 'X-API-Key: $KEY' http://localhost:8000/api/predictions/slates/<id>/refresh`.
4. If the worker is *not* healthy, restart it:
   `docker compose restart worker`.
5. If both still fail, walk through `docs/operations.md > Worker
   Operations` for the manual recovery path.

### Symptom: `last_ingest_age_seconds` > 129600 (36h)

Means: no source ingest completed in the last 36 hours.

1. List enabled sources:
   `docker compose exec proai python -m app.cli list-sources`.
2. Look at the most recent rows in `source_documents` for that source.
   A bunch of 4xx/5xx in `error_message` points at upstream.
3. If the source connector profile is wrong, fix it in
   `app/services/sources/<connector>.py` and redeploy. Profile errors
   are surfaced in `/api/health > operational_signals.unregistered_parser_sources`.
4. For Sudamericana / Argentine / Colombian leagues, no connector exists
   yet (see [proAI data sources](../../../.claude/projects/-home-silver/memory/project_proai_data_sources.md)).

### Symptom: `worker_state != "healthy"`

Means: the scheduler-worker container last reported a degraded state.

1. `docker compose ps worker` — is it running?
2. `docker compose logs --tail 200 worker` — read the latest cycle log.
3. Common cause: a long-running training job that exceeded the cycle
   budget. Look for "training" entries with `duration_ms > 600000`.
4. Restart: `docker compose restart worker`. The next cycle should reset
   the state.

### Symptom: pre-Friday rebuild needed

Means: code change merged Monday-Thursday and the user wants the bake to
reach the live container.

Per the docker rebuild gotcha:
**both `proai` and `worker` images must be rebuilt** — they tag separately.

```
docker compose build proai worker
docker compose up -d proai worker
```

Then verify:

```
curl -s http://localhost:8000/api/health | jq '.cache_version'
```

The hash should change after every successful build.

### Symptom: `268` tests dropped below `268`

Means: a code change broke either the math helpers, the metric helpers,
the API workflow, or the e2e pipeline.

1. `docker compose exec -w /app/backend proai python -m pytest tests/ -x`
2. Walk back from the first failure. The pure helper failures
   (`test_model_training_math.py`, `test_model_training_metrics.py`) are
   table-driven — if they break, the math itself changed and there
   should be an artifact migration note in the commit.

## Backup recovery

`postgres-backup` runs a `pg_dump` once per 24h into the `proai-backups`
volume. To restore:

```
docker compose down proai worker
docker compose run --rm postgres-backup pg_restore --clean --if-exists \
    -d $PROAI_DATABASE_URL /backups/<file>.dump
docker compose up -d
```

Do not skip the `--clean` flag — without it, the restore will collide on
existing foreign keys.
