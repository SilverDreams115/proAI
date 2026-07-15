# proAI

Plataforma de predicción deportiva para quinielas Progol. Ingiere estadísticas de fútbol, normaliza entidades, genera probabilidades `1/X/2` con XGBoost y produce boletas auditables con cobertura de riesgo.

## Principios

- Evidencia antes que opinión: cada predicción preserva su trail de fuentes.
- Trazabilidad: cada output es explicable (feature map, banda de confianza, rationale).
- No ocultar incertidumbre: los partidos sin datos suficientes aparecen como `low` o `blocked`, nunca inflados artificialmente.

## Stack

| Componente | Tecnología |
|---|---|
| API | FastAPI + Python 3.12 |
| Base de datos | PostgreSQL 16 (SQLite para tests) |
| ML | XGBoost CPU-only (sin sklearn) |
| Worker | Scheduler separado en Docker |
| Auth | Session cookie HMAC-signed + API key |
| Infra | Docker Compose |

## Levantar localmente

```bash
cp .env.example .env          # ajustar credenciales
docker compose up --build     # primera vez
make up                       # usos siguientes
```

Dashboard en `http://127.0.0.1:8000/`.

## Tests y calidad

```bash
cd backend
.venv/bin/python -m pytest tests/ -q   # 1031 tests
.venv/bin/ruff check app/ tests/       # linter
.venv/bin/mypy app/                    # tipos
make test-fast                         # unit/pure tests, feedback rapido
make test-integration                  # ASGI/API/DB workflows no lentos
make test-slow                         # entrenamientos/backtests amplios
make test                              # suite completa
make check                             # lint + typecheck + test-fast
```

## Rebuild tras cambios de código

El código está baked en la imagen Docker. Después de cualquier cambio:

```bash
docker compose build proai worker
docker compose up -d proai worker
```

## Estructura

```
backend/           API, servicios, repositorios, workers, tests
frontend/          UI de revisión y monitoring
docs/              Documentación técnica y operativa
data/              Contexto Progol local (current.json)
scripts/           Bootstrap, smoke tests, utilidades
deploy/            Configuración de producción (Caddy, backups)
```

## Documentación

- [docs/architecture.md](docs/architecture.md) — flujo end-to-end, clasificación de módulos, composition_hash
- [docs/ml_pipeline.md](docs/ml_pipeline.md) — XGBoost, bandas de confianza, retraining gate, neural baseline
- [docs/operations.md](docs/operations.md) — comandos operativos completos
- [docs/security.md](docs/security.md) — auth, rutas protegidas, checklist de producción
- [docs/data_quality.md](docs/data_quality.md) — resultados canónicos, slates legacy, anchor gap

## Advertencias de operación

> **No reentrenar sin pasar por el gate de readiness.** Siempre ejecutar `GET /api/training/adaptive/readiness` y `POST /api/training/adaptive/dry-run` antes de `/run`.

> **No modificar datos de PG-2336 ni slates activas manualmente.** Usar únicamente los endpoints de API con autenticación.

> **No relajar thresholds de confianza.** Un partido con datos insuficientes se muestra como `low` — es el comportamiento correcto.

## Variables de entorno principales

| Variable | Descripción |
|---|---|
| `PROAI_ENVIRONMENT` | `development` / `production` |
| `PROAI_DATABASE_URL` | URL de PostgreSQL |
| `PROAI_AUTH_REQUIRED` | `true` en producción |
| `PROAI_AUTH_API_KEY` | API key para scripts y automation |
| `PROAI_AUTH_PASSWORD_HASH` | Hash PBKDF2 de la contraseña del dashboard |
| `PROAI_SESSION_SECRET` | Secret para firmar cookies de sesión |
| `PROAI_DOCS_ENABLED` | `false` en producción |
| `PROAI_ENABLE_WORKER_ROUTES` | `false` en producción |
| `PROAI_FOOTBALL_DATA_API_KEY` | API key de football-data.org |

Ver `.env.example` para la lista completa.

## Producción con proxy y backups

```bash
cp deploy/production.env.example .env
docker compose -f docker-compose.prod.yml up -d
```

Stack de producción incluye Caddy como proxy TLS y job de backups de PostgreSQL. Ver [docs/operations.md](docs/operations.md) para restore.

## ML stack

XGBoost es la única librería ML del runtime. scikit-learn está explícitamente excluido. Ver [docs/ml_pipeline.md](docs/ml_pipeline.md) para detalles del pipeline.

## Endpoints de referencia rápida

| Endpoint | Descripción |
|---|---|
| `GET /api/health` | Health operativo |
| `GET /api/ready` | Readiness probe |
| `GET /api/metrics` | Prometheus |
| `GET /api/predictions/slates/{id}` | Predicciones de la slate |
| `GET /api/predictions/slates/{id}/ticket` | Boleta recomendada |
| `GET /api/predictions/slates/{id}/quality` | Quality report con anchor gap |
| `GET /api/operations/publication-gate` | Gate unificado publicar/jugar/ML |
| `GET /api/training/adaptive/readiness` | Gate de readiness para retraining |
| `POST /api/training/adaptive/dry-run` | Simulación de retraining |
