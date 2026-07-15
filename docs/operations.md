# proAI — Runbook de Operaciones

## Stack local

```bash
# Levantar todo
make up                          # docker compose up -d (postgres + proai + worker)

# Parar
make down

# Restart
make restart

# Rebuild + restart (obligatorio después de cambios de código)
docker compose build proai worker
docker compose up -d proai worker

# Estado
docker compose ps
```

> **Importante:** el código está baked en la imagen Docker, no montado como volumen. Cualquier cambio de código requiere rebuild de **ambas** imágenes (`proai` y `worker`) antes de ser efectivo en el contenedor.

---

## Health

```bash
# Health endpoint
curl http://localhost:8000/api/health | python3 -m json.tool
make health

# Readiness
curl http://localhost:8000/api/ready
make ready

# Métricas Prometheus
curl http://localhost:8000/api/metrics

# Check completo de producción
make production-check
```

`make production-check` valida: readiness, alineación de SCHEMA_VERSION con Alembic, fuentes activas, y configuración de producción.

---

## Logs

```bash
docker compose logs proai
docker compose logs worker
docker compose logs proai --tail=100 --follow
```

Los logs son JSON estructurado en producción. Cada request incluye `X-Request-ID`.

---

## Tests

```bash
cd backend
.venv/bin/python -m pytest tests/ -q          # suite completa
.venv/bin/python -m pytest tests/ -q --tb=short  # con detalle en fallos
.venv/bin/ruff check app/ tests/              # linter
.venv/bin/mypy app/                           # type checking

# Desde el root
make lint
make typecheck
make test
make check  # lint + typecheck + test
```

---

## Actualizar contexto Progol

```bash
# Actualizar current.json desde fuente externa
make update-current-context

# Reexportar current.json desde slates activas ya validadas en DB
make update-current-context-from-db

# Auditar slates activas: gate, placeholders, bloqueos y frescura
make audit-current

# Refrescar slate activa en el contenedor
make refresh-current

# Asegurar que el job de refresh existe en el scheduler
make ensure-current-job
```

---

## Ingestion y fuentes

```bash
# Bootstrap de fuentes (idempotente — seguro re-ejecutar)
docker compose exec -T proai bash -c "cd /app/backend && python3 -m scripts.bootstrap_thesportsdb_sources 2>&1"
docker compose exec -T proai bash -c "cd /app/backend && python3 -m scripts.bootstrap_football_data_sources 2>&1"

# Verificar fuentes registradas
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" http://localhost:8000/api/sources

# Forzar ingestion manual de una fuente
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" http://localhost:8000/api/ingestion/runs \
  -H "Content-Type: application/json" \
  -d '{"source_id": "<uuid>"}'
```

---

## Predicciones

```bash
# Obtener predicciones de la slate activa
SLATE_ID="<uuid>"
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID

# Refrescar predicciones
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID/refresh

# Quality report (anchor gap + confianza por partido)
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/predictions/slates/$SLATE_ID/quality

# Reporte de confianza completo
make confidence-report

# Reporte desde el stack Docker, escribiendo en ./reports del host
make confidence-report-docker
```

---

## Gate operativo unificado

Antes de publicar, compartir o jugar una slate con dinero real, revisar el gate
unificado. Es solo lectura y combina Money Mode, deuda de datos, placeholders,
posiciones bloqueadas y readiness de aprendizaje.

```bash
# Todas las slates activas/próximas
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/operations/publication-gate

# Una slate específica
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  "http://localhost:8000/api/operations/publication-gate?slate_id=$SLATE_ID"
```

Estados:

- `DO_NOT_PLAY`: no jugar; resolver bloqueadores antes de publicar.
- `PLAY_CONSERVATIVE_ONLY`: solo boleto conservador, con cautela.
- `READY_TO_PLAY`: gate limpio para el boleto recomendado.
- `REVIEW_REQUIRED`: falta revisión operativa antes de publicar.

La activación de ML queda bloqueada mientras `learning_gate.training_ready` sea
false, aunque existan candidatos experimentales.

---

## Scoring

Solo ejecutar después de tener resultados canónicos de todos los partidos:

```bash
# Computar scoring de una jornada
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/scoring/slates/$SLATE_ID/compute

# Ver histórico de scoring
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/scoring/history

# CLI equivalente
make calibration   # evaluación de calibración
make evaluate      # walk-forward evaluation
```

---

## Retraining

Siempre seguir este flujo:

```bash
# 1. Verificar readiness
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/readiness

# 2. Dry-run (simula sin persistir)
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/dry-run

# 3. Ejecutar solo si los gates pasan
curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/adaptive/run
```

El endpoint `/run` devuelve 409 si la readiness falla. No forzar el retraining ignorando el gate.

---

## Neural baseline (experimental)

```bash
# Solo para introspección — nunca en producción
curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/readiness

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/training/neural/dry-run
```

Los artefactos generados tienen `is_production=False`. No afectan predicciones activas.

---

## Worker routes

```bash
# Solo disponibles cuando PROAI_ENABLE_WORKER_ROUTES=true (dev)
# Requieren auth cuando hay credenciales configuradas

curl -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/worker/scheduler/status

curl -X POST -H "X-API-Key: $PROAI_AUTH_API_KEY" \
  http://localhost:8000/api/worker/scheduler/run-once
```

En producción `PROAI_ENABLE_WORKER_ROUTES=false` y las rutas no están registradas.

---

## Schema e integridad de datos

```bash
# Verificar alineación de SCHEMA_VERSION
make production-check

# Ver migraciones aplicadas
docker compose exec proai bash -c \
  "cd /app/backend && python3 -c 'from app.db.migrations import SCHEMA_VERSION; print(SCHEMA_VERSION)'"
```

SCHEMA_VERSION actual: **19**. Si se añade una migración, el número debe incrementarse en `migrations.py` y añadirse la revisión Alembic correspondiente en `backend/alembic/versions/`.

---

## Autenticación local

```bash
# Generar hash de contraseña
.venv/bin/python backend/scripts/hash_password.py
# → copiar el hash en .env como PROAI_AUTH_PASSWORD_HASH='<hash>'

# Login (obtiene cookie de sesión)
curl -c cookies.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password": "tu-password"}'

# Usar sesión en requests siguientes
curl -b cookies.txt http://localhost:8000/api/slates
```

---

## Smoke tests

```bash
make frontend-smoke    # valida que frontend assets se sirven
make load-smoke        # prueba de carga básica (latencia por percentil)
```

---

## Boot automático

El stack usa `restart: unless-stopped`. Se reinicia tras un reboot si el daemon Docker arranca automáticamente:

```bash
systemctl is-enabled docker
sudo systemctl enable --now docker  # si no está habilitado
make up                             # levantar el proyecto una vez
```

---

## Backup y restore

```bash
# Stack con Caddy + backups programados
docker compose -f docker-compose.prod.yml up -d

# Restore desde backup
docker compose -f docker-compose.prod.yml exec -T postgres sh -c \
  'gunzip -c /backups/proai-YYYYMMDDTHHMMSSZ.sql.gz | psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```
