# proAI — Seguridad

## Modelo de autenticación

proAI usa autenticación a nivel de middleware HTTP para la mayoría de rutas, con guards per-route adicionales en endpoints sensibles.

### Métodos soportados

| Método | Header / Cookie |
|---|---|
| API Key | `X-API-Key: <token>` |
| Bearer token | `Authorization: Bearer <token>` |
| Session cookie | `proai_session` (HttpOnly, SameSite=Lax, TTL configurable) |

La sesión se crea via `POST /api/auth/login` con password. El hash de contraseña se almacena como PBKDF2-SHA256 (`PROAI_AUTH_PASSWORD_HASH`).

---

## Rutas públicas

Las siguientes rutas no requieren autenticación en ningún entorno:

| Ruta | Propósito |
|---|---|
| `GET /api/health` | Health check — puede llamarlo un load balancer |
| `GET /api/ready` | Readiness probe |
| `GET /api/metrics` | Métricas Prometheus |
| `GET /api/auth/session` | Verificar estado de sesión activa |
| `POST /api/auth/login` | Obtener sesión |
| `POST /api/auth/logout` | Cerrar sesión |

> `/api/metrics` es público para facilitar integración con sistemas de monitoring. Si el entorno es sensible, poner Caddy o un proxy delante que restrinja el acceso.

---

## Rutas protegidas

Cuando `PROAI_AUTH_REQUIRED=true`, el middleware rechaza con 401 cualquier request no autenticado a rutas no listadas arriba. Esto cubre:

- `/api/slates` y subrutas
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

`POST /api/worker/scheduler/run-once` y `GET /api/worker/scheduler/status` tienen un guard per-route adicional (`require_worker_auth` en `app/api/deps.py`) que actúa independientemente del flag global `PROAI_AUTH_REQUIRED`:

- **Sin credenciales configuradas** (`PROAI_AUTH_API_KEY=None` y `PROAI_SESSION_SECRET=None`): guard es no-op — postura bare-dev.
- **Con credenciales configuradas**: requiere API key o sesión válida aunque `PROAI_AUTH_REQUIRED=false`.
- **En producción**: `PROAI_ENABLE_WORKER_ROUTES=false` por defecto — las rutas no están registradas. El validador de configuración de producción rechaza `enable_worker_routes=true`.

---

## OpenAPI schema

`GET /api/openapi-schema` tiene el mismo guard per-route que las worker routes. El endpoint expone la superficie completa de la API (rutas, schemas, modelos) — no debe ser accesible sin autenticación en entornos con credenciales:

- **Sin credenciales**: accesible (bare-dev).
- **Con credenciales**: requiere API key o sesión, independientemente de `PROAI_AUTH_REQUIRED`.
- **`PROAI_DOCS_ENABLED=false`** (producción): Swagger UI (`/docs`) y `/openapi.json` están deshabilitados. El schema JSON solo es accesible via `/api/openapi-schema` con credenciales válidas.

---

## Política bare-dev

Cuando no hay credenciales configuradas en absoluto (`PROAI_AUTH_API_KEY` y `PROAI_SESSION_SECRET` son None), el sistema opera en postura completamente abierta — coherente con un entorno de desarrollo limpio sin secretos. **Nunca usar esta postura en un entorno expuesto a red.**

---

## Variables de entorno sensibles

| Variable | Notas |
|---|---|
| `PROAI_AUTH_API_KEY` | Nunca imprimir en logs. Comparar siempre con `secrets.compare_digest`. |
| `PROAI_AUTH_PASSWORD_HASH` | Contiene `$` — usar comillas simples en `.env`. |
| `PROAI_SESSION_SECRET` | Mínimo 32 caracteres. Rotar implica invalidar todas las sesiones activas. |
| `POSTGRES_PASSWORD` | Separado del auth de la API. |
| `PROAI_FOOTBALL_DATA_API_KEY` | API key de tercero — no commitear. |

`.env` está en `.gitignore`. No añadir secretos reales a ningún archivo versionado.

---

## CORS

```
PROAI_CORS_ALLOWED_ORIGINS=https://tu-dominio.com
```

No usar wildcard (`*`) en producción si la API usa cookies de sesión — `allow_credentials=True` es incompatible con `allow_origins=["*"]` según la especificación CORS.

---

## Rate limiting

El middleware aplica rate limiting global por cliente (por IP o `X-Forwarded-For`) antes de evaluar auth. Esto previene quemado de CPU en el path de autenticación bajo carga hostil. Configurable con:

```
PROAI_RATE_LIMIT_MAX_REQUESTS=...
PROAI_RATE_LIMIT_WINDOW_SECONDS=...
```

---

## Login throttling

`POST /api/auth/login` tiene un límite de intentos fallidos por IP (constante `LOGIN_FAILURE_LIMIT` en `auth.py`). Después del límite, el endpoint devuelve 429 aunque la contraseña sea correcta. Se resetea al reiniciar el proceso.

---

## Producción: checklist mínimo

- [ ] `PROAI_AUTH_REQUIRED=true`
- [ ] `PROAI_AUTH_API_KEY` no placeholder
- [ ] `PROAI_AUTH_PASSWORD_HASH` válido (generado con `hash_password.py`)
- [ ] `PROAI_SESSION_SECRET` ≥ 32 caracteres, no placeholder
- [ ] `PROAI_DOCS_ENABLED=false`
- [ ] `PROAI_ENABLE_WORKER_ROUTES=false`
- [ ] `PROAI_ENVIRONMENT=production`
- [ ] `PROAI_ALLOWED_HOSTS` acotado (no `*`)
- [ ] `PROAI_FORCE_HTTPS=true` si el proxy termina TLS
- [ ] `.env` fuera del repo y con permisos `600`

`make production-check` valida la mayoría de estos puntos.
