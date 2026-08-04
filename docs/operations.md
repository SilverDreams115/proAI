# proAI — Operations Runbook

## Local stack

```bash
# Bring up everything
make up                          # docker compose up -d (postgres + proai + worker)

# Stop
make down

# Restart
make restart

# Rebuild + restart (mandatory after code changes)
docker compose build proai worker
docker compose up -d proai worker

# Status
docker compose ps
```

> **Important:** the code is baked into the Docker image, not mounted as a volume. Any code change requires rebuilding **both** images (`proai` and `worker`) before it takes effect in the container.

---

## Health

```bash
# Health endpoint
curl http://localhost:8000/api/health | python3 -m json.tool
make health

# Readiness
curl http://localhost:8000/api/ready
make ready

# Prometheus metrics
curl http://localhost:8000/api/metrics

# Full production check
make production-check
```

`make production-check` validates: readiness, SCHEMA_VERSION alignment with Alembic, active sources, and production configuration.

---

## Logs

```bash
docker compose logs proai
docker compose logs worker
docker compose logs proai --tail=100 --follow
```

Logs are structured JSON in production. Each request includes `X-Request-ID`.

---

## Tests

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # full suite
.venv/bin/python -m pytest tests/ -q --tb=short  # with failure detail
.venv/bin/ruff check app/ tests/              # linter
.venv/bin/mypy app/                           # type checking

# From the root
make lint
make typecheck
make test
make check  # lint + typecheck + test
```

---

## Update the Progol context

```bash
# Update current.json from an external source
make update-current-context

# Re-export current.json from active slates already validated in the DB
make update-current-context-from-db

# Audit active slates: gate, placeholders, blocks and freshness
make audit-current

# Refresh the active slate in the container
make refresh-current

# Ensure the refresh job exists in the scheduler
make ensure-current-job
```

---

## Ingestion and sources

```bash
# Source bootstrap (idempotent — safe to re-run)
docker compose exec -T proai bash -c "cd /app/backend && python3 -m scripts.bootstrap_thesportsdb_sources 2>&1"
docker compose exec -T proai bash -c "cd /app/backend && python3 -m scripts.bootstrap_football_data_sources 2>&1"

# Verify registered sources
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" http://localhost:8000/api/sources

# Force a manual ingestion of a source
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" http://localhost:8000/api/ingestion/runs \
  -H "Content-Type: application/json" \
  -d '{"source_id": "<uuid>"}'
```

### TheSportsDB free tier: round-walking under-collects

The free key (`3`) caps **`eventsround.php` and `eventsseason.php` at 5 events
per call**. A round with more fixtures than that is silently truncated — Liga
MX rounds hold 9 and MLS rounds 15, so round-walking those leagues loses 44%
and 67% of every jornada. Verified 2026-08-03: both leagues had only the
Progol fixtures for the first weekend of August, because nothing else fit
under the cap.

`eventsday.php` is **not** capped. Sources for high-fixture leagues therefore
carry `strategy=day&days_back=<N>` in `base_url`, which walks one calendar day
at a time (2s apart, ~`N` requests per run):

```
.../json/3?league=4350&seasons=...&strategy=day&days_back=21
```

Pick `days_back` to cover the refresh interval with margin — 21 days against a
weekly job. Round-walking is still fine for competitions whose rounds hold ≤5
fixtures, and is much cheaper: one-off historical loads (`backfill=1`) use it
because walking years of days would cost thousands of requests.

Every new source needs a scheduler job or it ingests once and freezes:

```bash
docker compose exec -T postgres psql -U proai -d proai \
  -c "select s.name, (j.id is not null) as has_job
      from sources s left join scheduled_ingestion_jobs j on j.source_id = s.id
      where s.is_active order by has_job, s.name;"
```

---

## Predictions

```bash
# Get the active slate's predictions
SLATE_ID="<uuid>"
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID

# Refresh predictions
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID/refresh

# Quality report (anchor gap + per-match confidence)
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID/quality

# Full confidence report
make confidence-report

# Distribution diagnostic for one slate: spots systemic problems the
# per-match view hides — everything went to the visitor, many near-zero
# class probabilities, a FIJO sitting on a low-evidence match. Reads the
# live API by default, or a saved predictions payload with --input.
docker compose exec --workdir /app/backend proai \
  python -m scripts.slate_diagnostic_report --from-db --slate-id $SLATE_ID

# Per-match reasons behind LISTO / REVISAR / BLOQUEADO for the active
# slates, plus suspicious names and safe candidates. Same report the
# publication gate consumes, on the command line.
docker compose exec --workdir /app/backend proai \
  python -m scripts.active_slate_readiness_report

# Report from the Docker stack, writing to the host's ./reports
make confidence-report-docker
```

---

## Unified operational gate

Before publishing, sharing or playing a slate with real money, review the unified
gate. It is read-only and combines Money Mode, data debt, placeholders,
blocked positions and learning readiness.

```bash
# All active/upcoming slates
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/operations/publication-gate

# A specific slate
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  "http://localhost:8000/api/operations/publication-gate?slate_id=$SLATE_ID"
```

States:

- `DO_NOT_PLAY`: do not play; resolve blockers before publishing.
- `PLAY_CONSERVATIVE_ONLY`: conservative ticket only, with caution.
- `READY_TO_PLAY`: clean gate for the recommended ticket.
- `REVIEW_REQUIRED`: operational review needed before publishing.

ML activation stays blocked while `learning_gate.training_ready` is
false, even if experimental candidates exist.

---

## Scoring

Only run after having canonical results for all matches:

```bash
# Compute scoring for a jornada
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/scoring/slates/$SLATE_ID/compute

# View scoring history
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/scoring/history

# CLI equivalent
make calibration   # calibration evaluation
make evaluate      # walk-forward evaluation
```

---

## Retraining

Always follow this flow:

```bash
# 1. Check readiness
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/readiness

# 2. Dry-run (simulates without persisting)
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/dry-run

# 3. Run only if the gates pass
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/run
```

The `/run` endpoint returns 409 if readiness fails. Do not force retraining ignoring the gate.

---

## Neural baseline (experimental)

```bash
# Read-only introspection
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/readiness

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/dry-run

# Leave-one-slate-out: one training run per slate, so slower than dry-run.
# This is the number to judge a candidate by — dry-run's `comparison` block
# scores the model on the rows it trained on.
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/loso

# Candidate → promote → rollback. Promotion is gated on out-of-sample
# evidence for the served (temperature-calibrated) vector; see
# docs/ml_pipeline.md. `force=true` bypasses the gate — do not use it to
# push a candidate that failed.
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/candidates/train

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"candidate_run_id": "<id>"}' \
  http://localhost:8000/api/training/neural/promote

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/rollback
```

The generated artifacts have `is_production=False`. A promoted artifact runs as
a read-only shadow on prediction responses (`neural_shadow`): it adds a
temperature-calibrated view of the same probabilities and, being monotone per
row, can never change the served pick or touch the ticket optimizer.

---

## Capturing a concurso before LN publishes its guía

When a concurso is live and sellable but the LN guide PDF is not out yet, the
scrapers have nothing to observe and `/api/slates/visible` will not list the
slate — it only surfaces official lineage. An operator can transcribe the
programa from an official source (LN's own page, or TuLotero as a licensed
reseller) and record it as a validated proposal:

```bash
# capture.json: draw_code, week_type, source_url, capture_note,
# registration_closes_at, fixtures[{position, home, away}]
docker compose exec --workdir /app/backend proai \
  python -m scripts.capture_operator_proposal --file /tmp/capture.json --dry-run

docker compose exec --workdir /app/backend proai \
  python -m scripts.capture_operator_proposal --file /tmp/capture.json \
  --apply --confirm CAPTURE-OPERATOR-PROPOSAL
```

The source URL must be an official host or the capture is refused. `capture_note`
records where the fixture strings were actually read from — the URL attests the
concurso's lineage, not that anything parsed that page. The row lands
`validated`; promoting it into a slate is still the normal
`POST /api/slates/proposed/{id}/promote`.

---

## Worker routes

```bash
# Only available when PROAI_ENABLE_WORKER_ROUTES=true (dev)
# Require auth when credentials are configured

curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/worker/scheduler/status

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/worker/scheduler/run-once
```

In production `PROAI_ENABLE_WORKER_ROUTES=false` and the routes are not registered.

---

## Schema and data integrity

```bash
# Verify SCHEMA_VERSION alignment
make production-check

# View applied migrations
docker compose exec proai bash -c \
  "cd /app/backend && python3 -c 'from app.db.migrations import SCHEMA_VERSION; print(SCHEMA_VERSION)'"
```

Current SCHEMA_VERSION: **32**. If a migration is added, the number must be incremented in `migrations.py` and the corresponding Alembic revision added under `backend/alembic/versions/`.

---

## Local authentication

```bash
# Generate a password hash
.venv/bin/python backend/scripts/hash_password.py
# → copy the hash into .env as PROAI_AUTH_PASSWORD_HASH='<hash>'

# Login (gets a session cookie)
curl -c cookies.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password": "your-password"}'

# Use the session on subsequent requests
curl -b cookies.txt http://localhost:8000/api/slates
```

---

## Smoke tests

```bash
make frontend-smoke    # validates that frontend assets are served
make load-smoke        # basic load test (per-percentile latency)
```

---

## Automatic boot

The stack uses `restart: unless-stopped`. It restarts after a reboot if the Docker daemon starts automatically:

```bash
systemctl is-enabled docker
sudo systemctl enable --now docker  # if not enabled
make up                             # bring up the project once
```

---

## Backup and restore

```bash
# Stack with Caddy + scheduled backups
docker compose -f docker-compose.prod.yml up -d

# Restore from backup
docker compose -f docker-compose.prod.yml exec -T postgres sh -c \
  'gunzip -c /backups/proai-YYYYMMDDTHHMMSSZ.sql.gz | psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
