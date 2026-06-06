# proAI Operations

This project is designed to run locally with a production-like Docker stack:
FastAPI, PostgreSQL, and a separate scheduler worker.

## Start and Stop

```bash
make up
make down
make restart
```

Open the dashboard at:

```text
http://127.0.0.1:8000/
```

## Health Checks

```bash
make health
make ready
make production-check
```

`production-check` must report `"ready": true` before treating the stack as
production-like.

The runtime migration path is `backend/app/db/migrations.py`; Alembic revisions
under `backend/alembic/versions` are the audited review trail. When a schema
change is added, update both paths and keep the numeric Alembic revision aligned
with `SCHEMA_VERSION`. `production-check` validates that alignment.

## Authentication

The local production profile enables API authentication for `/api` routes except
health and readiness. The static dashboard uses password login and a signed
`HttpOnly` session cookie. `PROAI_AUTH_API_KEY` is still supported for scripts,
automation, and smoke tests.

Generate a new password hash before changing `PROAI_AUTH_PASSWORD_HASH`:

```bash
.venv/bin/python backend/scripts/hash_password.py
```

Store the generated hash in `.env` with single quotes because PBKDF2 hashes
contain `$` separators.

Keep `.env` private. It is ignored by git.

## Worker Operations

The scheduler worker runs as its own Docker service. Worker HTTP control routes
should stay disabled in production-like mode.

Use CLI commands from the API container for admin actions:

```bash
make refresh-current
make ensure-current-job
make calibration
make evaluate
```

## Boot on Machine Startup

The Compose services use `restart: unless-stopped`. That means they come back
after a reboot as long as the Docker daemon starts automatically.

Check Docker daemon startup:

```bash
systemctl is-enabled docker
```

Enable it on a systemd machine:

```bash
sudo systemctl enable --now docker
```

Then start the project once:

```bash
make up
```

Do not run `make down` if you want Docker to restart these containers after the
next boot; `down` intentionally stops and removes the Compose containers.

## Data Safety

PostgreSQL data lives in the `proai-postgres-data` Docker volume. Application
data lives in `proai-data`. The mounted Progol context comes from:

```text
data/progol_context/current.json
```

Use the production Compose file when you need Caddy and scheduled backups:

```bash
docker compose -f docker-compose.prod.yml up -d
```
