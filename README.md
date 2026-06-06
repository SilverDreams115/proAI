# proAI

`proAI` is a platform for analyzing `Progol` fixtures, consolidating evidence
from web sources, and producing auditable probabilistic predictions.

## Principles

- Isolated project: it does not require editing other repositories in the workspace.
- Evidence before opinion: every prediction must preserve its source trail.
- Structured data first: the LLM interprets context, not replaces the model.
- Traceability: every output must be explainable.

## Structure

- `backend/`: API, domain, services, and analysis pipelines.
- `frontend/`: interface for review and monitoring.
- `docs/`: architecture, roadmap, and technical decisions.

## Initial goal

Build a solid foundation to:

1. capture match data and context from the internet
2. normalize and assess the quality of that information
3. generate `1 / X / 2` probabilities
4. expose suggested picks for `Progol` slates

## Local quality gate

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c backend/constraints.txt -e "./backend[dev]"
python -m ruff check backend frontend
python -m mypy --config-file backend/pyproject.toml backend/app/core backend/app/db backend/app/workers backend/app/api/routes/health.py
python -m pytest -q
```

## Runtime configuration

Main environment variables:

- `PROAI_ENVIRONMENT`
- `PROAI_DATABASE_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `PROAI_AUTH_REQUIRED`
- `PROAI_AUTH_API_KEY`
- `PROAI_AUTH_PASSWORD_HASH`
- `PROAI_SESSION_SECRET`
- `PROAI_AUTH_SESSION_COOKIE_NAME`
- `PROAI_AUTH_SESSION_TTL_SECONDS`
- `PROAI_API_HOST`
- `PROAI_API_PORT`
- `PROAI_LOG_LEVEL`
- `PROAI_LOG_JSON`
- `PROAI_ACCESS_LOG_ENABLED`
- `PROAI_DOCS_ENABLED`
- `PROAI_REQUEST_ID_HEADER`
- `PROAI_HEALTHCHECK_TIMEOUT_SECONDS`
- `PROAI_ALLOWED_HOSTS`
- `PROAI_CORS_ALLOWED_ORIGINS`
- `PROAI_FORCE_HTTPS`
- `PROAI_ENABLE_WORKER_ROUTES`
- `PROAI_WORKER_POLL_INTERVAL_SECONDS`
- `PROAI_CURRENT_PROGOL_AUTO_REFRESH_ENABLED`
- `PROAI_CURRENT_PROGOL_REFRESH_INTERVAL_MINUTES`
- `PROAI_CURRENT_PROGOL_REFRESH_JOB_NAME`
- `PROAI_ALLOW_PICKLE_MODEL_ARTIFACTS`
- `PROAI_LIVE_PICK_READY_COMPETITIONS`
- `PROAI_LIVE_PICK_BLOCKED_COMPETITIONS`
- `PROAI_PUBLIC_HOSTNAME`
- `PROAI_HTTP_PORT`
- `PROAI_HTTPS_PORT`
- `PROAI_BACKUP_INTERVAL_SECONDS`
- `PROAI_BACKUP_RETENTION_DAYS`
- `PROAI_FOOTBALL_DATA_API_KEY`

Use [.env.example](/home/silver/projects/proAI/.env.example:1) as the baseline for deployment.

## ML stack

XGBoost is part of the base production runtime. It is the only ML
library proAI uses; scikit-learn is intentionally not supported. Local
installs and the production image include it out of the box:

```bash
python -m pip install -c backend/constraints.txt -e "./backend[dev]"
```

## Production container

Build and run directly:

```bash
docker build -t proai .
docker run --rm -p 8000:8000 --env-file .env -v proai-data:/data proai
```

Or with Compose:

```bash
cp .env.example .env
docker compose up --build
```

The production Compose stack now uses PostgreSQL as the primary database and runs the scheduler worker as a separate service.
The dashboard authenticates with a password and signed `HttpOnly` session cookie; API key auth remains available for automation.

## Local operations

The Docker stack persists PostgreSQL and application data in named volumes. The mounted
`data/progol_context/current.json` file is the local source for the active Progol contest.

Useful commands:

```bash
make up
make ready
make typecheck
make frontend-smoke
make load-smoke
make docker-build
make update-current-context
make refresh-current
make ensure-current-job
make calibration
make production-check
```

See [docs/operations.md](/home/silver/projects/proAI/docs/operations.md:1) for the
production-like local runbook, authentication notes, startup behavior, and data safety
details.

By default the worker creates a `current-progol-refresh` scheduled job and refreshes the
active Progol slate every 60 minutes. Tune it with `PROAI_CURRENT_PROGOL_REFRESH_INTERVAL_MINUTES`.
`make update-current-context` validates and enriches the mounted `current.json` before
the Docker refresh reads it.

For an edge-proxied production stack with backups:

```bash
cp deploy/production.env.example .env
docker compose -f docker-compose.prod.yml up -d
```

## Operational notes

- Health endpoint: `GET /api/health`
- Readiness endpoint: `GET /api/ready`
- Metrics endpoint: `GET /api/metrics`
- Auditable ticket endpoint: `GET /api/predictions/slates/{slate_id}/ticket`
- Per-competition backtest endpoint: `POST /api/training/models/evaluate/competitions`
- Logs are structured JSON by default in production.
- The container runs as a non-root user.
- Production authentication is enforced for all `/api` routes except health/readiness unless explicitly disabled. Browser users log in with a password; scripts can still send `X-API-Key`.
- Worker control routes are disabled by default in production.
- PostgreSQL is the default production backing store; SQLite remains useful for local development and tests.
- `docker-compose.prod.yml` adds a Caddy edge proxy with security headers and a PostgreSQL backup job.
- The backup job writes compressed dumps to the `proai-backups` volume and rotates them by retention days.
- CI runs lint, tests and image build on each push and pull request.
- Runtime startup still applies the lightweight built-in migrations, and Alembic
  revisions live under `backend/alembic` for audited production migration review.

## Restore workflow

Restore a backup from the production volume into PostgreSQL:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres sh -c \
  'gunzip -c /backups/proai-YYYYMMDDTHHMMSSZ.sql.gz | psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
