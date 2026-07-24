# proAI — Security

## Authentication model

proAI uses HTTP-middleware-level authentication for most routes, with additional per-route guards on sensitive endpoints.

### Supported methods

| Method | Header / Cookie |
|---|---|
| API Key | `X-API-Key: <token>` |
| Bearer token | `Authorization: Bearer <token>` |
| Session cookie | `proai_session` (HttpOnly, SameSite=Lax, configurable TTL) |

The session is created via `POST /api/auth/login` with a password. The password hash is stored as PBKDF2-SHA256 (`PROAI_AUTH_PASSWORD_HASH`).

---

## Public routes

The following routes do not require authentication in any environment:

| Route | Purpose |
|---|---|
| `GET /api/health` | Health check — a load balancer may call it |
| `GET /api/ready` | Readiness probe |
| `GET /api/metrics` | Prometheus metrics |
| `GET /api/auth/session` | Check active-session state |
| `POST /api/auth/login` | Get a session |
| `POST /api/auth/logout` | Close a session |

> `/api/metrics` is public to ease integration with monitoring systems. If the environment is sensitive, put Caddy or a proxy in front that restricts access.

---

## Protected routes

When `PROAI_AUTH_REQUIRED=true`, the middleware rejects with 401 any unauthenticated request to routes not listed above. This covers:

- `/api/slates` and subroutes
- `/api/predictions/*`
- `/api/training/*`
- `/api/ingestion/*`
- `/api/scoring/*`
- `/api/sources/*`
- `/api/results/*`
- `/api/stats/*`
- `/api/evidence/*`
- `/api/availability/*`
- `/api/scheduler/*`
- `/api/history/*`
- `/api/normalization/*`
- `/api/adaptive_datasets/*`

---

## Worker routes

`POST /api/worker/scheduler/run-once` and `GET /api/worker/scheduler/status` have an additional per-route guard (`require_worker_auth` in `app/api/deps.py`) that acts independently of the global `PROAI_AUTH_REQUIRED` flag:

- **No credentials configured** (`PROAI_AUTH_API_KEY=None` and `PROAI_SESSION_SECRET=None`): the guard is a no-op — bare-dev posture.
- **With credentials configured**: requires an API key or a valid session even if `PROAI_AUTH_REQUIRED=false`.
- **In production**: `PROAI_ENABLE_WORKER_ROUTES=false` by default — the routes are not registered. The production configuration validator rejects `enable_worker_routes=true`.

---

## OpenAPI schema

`GET /api/openapi-schema` has the same per-route guard as the worker routes. The endpoint exposes the full API surface (routes, schemas, models) — it must not be accessible without authentication in environments with credentials:

- **No credentials**: accessible (bare-dev).
- **With credentials**: requires an API key or a session, regardless of `PROAI_AUTH_REQUIRED`.
- **`PROAI_DOCS_ENABLED=false`** (production): Swagger UI (`/docs`) and `/openapi.json` are disabled. The JSON schema is only accessible via `/api/openapi-schema` with valid credentials.

---

## Bare-dev policy

When no credentials are configured at all (`PROAI_AUTH_API_KEY` and `PROAI_SESSION_SECRET` are None), the system operates in a fully open posture — consistent with a clean development environment without secrets. **Never use this posture in a network-exposed environment.**

---

## Sensitive environment variables

| Variable | Notes |
|---|---|
| `PROAI_AUTH_API_KEY` | Never print in logs. Always compare with `secrets.compare_digest`. |
| `PROAI_AUTH_PASSWORD_HASH` | Contains `$` — use single quotes in `.env`. |
| `PROAI_SESSION_SECRET` | Minimum 32 characters. Rotating it means invalidating all active sessions. |
| `POSTGRES_PASSWORD` | Separate from the API auth. |
| `PROAI_FOOTBALL_DATA_API_KEY` | Third-party API key — do not commit. |

`.env` is in `.gitignore`. Do not add real secrets to any versioned file.

---

## CORS

```
PROAI_CORS_ALLOWED_ORIGINS=https://your-domain.com
```

Do not use a wildcard (`*`) in production if the API uses session cookies — `allow_credentials=True` is incompatible with `allow_origins=["*"]` per the CORS specification.

---

## Rate limiting

The middleware applies global per-client rate limiting (by IP or `X-Forwarded-For`) before evaluating auth. This prevents CPU burn on the authentication path under hostile load. Configurable with:

```
PROAI_RATE_LIMIT_MAX_REQUESTS=...
PROAI_RATE_LIMIT_WINDOW_SECONDS=...
```

---

## Login throttling

`POST /api/auth/login` has a failed-attempt limit per IP (constant `LOGIN_FAILURE_LIMIT` in `auth.py`). After the limit, the endpoint returns 429 even if the password is correct. It resets when the process restarts.

---

## Production: minimum checklist

- [ ] `PROAI_AUTH_REQUIRED=true`
- [ ] `PROAI_AUTH_API_KEY` not a placeholder
- [ ] `PROAI_AUTH_PASSWORD_HASH` valid (generated with `hash_password.py`)
- [ ] `PROAI_SESSION_SECRET` ≥ 32 characters, not a placeholder
- [ ] `PROAI_DOCS_ENABLED=false`
- [ ] `PROAI_ENABLE_WORKER_ROUTES=false`
- [ ] `PROAI_ENVIRONMENT=production`
- [ ] `PROAI_ALLOWED_HOSTS` bounded (not `*`)
- [ ] `PROAI_FORCE_HTTPS=true` if the proxy terminates TLS
- [ ] `.env` outside the repo and with `600` permissions

`make production-check` validates most of these points.
