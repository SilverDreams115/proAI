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
# Introspection only — never in production
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/readiness

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/dry-run
```

The generated artifacts have `is_production=False`. They do not affect active predictions.

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
