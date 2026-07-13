# proAI — Arquitectura Técnica

## Visión general

proAI es una plataforma de predicción deportiva para quinielas Progol. Ingiere estadísticas de fútbol desde fuentes estructuradas, normaliza entidades, genera probabilidades `1/X/2` mediante un modelo XGBoost calibrado, y produce boletas auditables con cobertura de riesgo. Todo output es trazable: cada predicción conserva su feature map completo, su banda de confianza y su hash de composición.

---

## Flujo end-to-end

```
Fuentes externas (TheSportsDB, football-data.org, football-data.co.uk)
        │
        ▼
[IngestionService]  ─── normaliza equipos/competiciones
        │               entity resolution, aliases, deduplicación
        ▼
[DB: stats / results / evidence]
        │
        ▼
[FeatureService]  ─── ventana de forma reciente, H2H, goles, Elo
        │
        ▼
[ModelTrainingService / XGBoost artifact]
        │
        ▼
[PredictionService]  ─── probabilidades 1/X/2
        │               banda de confianza (blocked/low/medium/high)
        │               rationale + anchor gap diagnostic
        ▼
[TicketRecommendationService + TicketOptimizer]
        │               Simple / Dobles / Completa
        │               cobertura de riesgo (Poisson Binomial)
        ▼
[Slate activa — PG-xxxx]
        │
        ▼ (después del partido)
[ResultService / canonical_result]
        │
        ▼
[JornadaScoringService]  ─── hit-rate, Brier score
        │
        ▼
[AdaptiveDatasetService]  ─── filas de entrenamiento auditadas
        │
        ▼
[AdaptiveRetrainingService]  ─── gate de readiness → retrain XGBoost
```

---

## Componentes principales

### FastAPI (app/main.py)
Servidor principal. Registra 20 routers bajo `/api/`. Auth por middleware (API key o session cookie firmada). Rutas worker y openapi-schema con guard per-route adicional.

### PostgreSQL
Base de datos principal. `SCHEMA_VERSION = 19`. Migraciones automáticas en startup (`app/db/migrations.py`), con Alembic como trail de auditoría (`backend/alembic/`). Nunca hacer cambios de schema fuera de este mecanismo.

### Worker (app/workers/scheduler_worker.py)
Proceso separado. Ejecuta jobs programados: ingestion refresh, archive/observe/auto-promote del pipeline Progol. Controlable via `POST /api/worker/scheduler/run-once` (requiere auth cuando hay credenciales).

### IngestionService
Núcleo del pipeline de datos. Orquesta connectors → parsers → normalization → entity resolution → persistencia. 10+ servicios dependen de él. **No modificar sin tests exhaustivos.**

### PredictionService
Genera probabilidades y bandas de confianza. Contiene el modelo XGBoost cargado, la lógica Poisson, el cálculo de anchor, el rationale y el diagnóstico de anchor gap. **Código más crítico del sistema.**

### TicketOptimizer
Selecciona la boleta óptima dados los picks y los parámetros de cobertura. Determinista y auditado. **No modificar sin demostrar equivalencia exacta.**

### SchedulerService / CurrentProgolService
Gestionan el ciclo de vida de la slate activa: detect → observe → propose → auto-promote. Driver del flujo Progol.

### AdaptiveDataset + RetrainingGate
Acumulan resultados de jornadas completas como dataset de entrenamiento. La gate evalúa readiness antes de permitir un retrain real. Ver `docs/ml_pipeline.md`.

### NeuralBaselineService
Experimental offline. `is_production=False`. No integrado en predicciones de producción.

---

## Clasificación de módulos

### DO_NOT_TOUCH_CRITICAL
Cambios aquí sin tests fuertes pueden romper predicciones, tickets o integridad de datos silenciosamente.

| Módulo | Razón |
|---|---|
| `prediction_service.py` | Probabilidades, bandas, rationale, persistencia |
| `feature_service.py` | Feature engineering del modelo XGBoost |
| `ticket_optimizer.py` | Selección de boleta — determinismo auditado |
| `ticket_recommendation_service.py` | Cobertura + recomendación |
| `ingestion_service.py` | Pipeline de datos — 10+ dependientes |
| `model_training_service.py` | XGBoost train/eval/walk-forward |
| `model_training_artifacts.py` | I/O de artefactos — serialización crítica |
| `model_training_math.py` | Fundamentos estadísticos |
| `model_training_metrics.py` | Métricas walk-forward |
| `current_progol_service.py` | Contexto activo Progol — driver del worker |
| `slate_proposal_service.py` | Pipeline observe→auto-promote |
| `normalization_service.py` | Nombres canónicos — afecta todos los datos |
| `scheduler_service.py` | Jobs programados |
| `slate_service.py` | CRUD core de slates |
| `calibration.py` | Calibración isotónica PAV |
| `drift.py` | PSI — drift detection |
| `coverage.py` | Poisson Binomial — cobertura de ticket |

### ACTIVE (auxiliares wired)
Activos y necesarios, pero modificables con menor riesgo si hay tests.

| Módulo | Rol |
|---|---|
| `narrative_interpretation_service.py` | Extracción de señales de texto para evidence |
| `adaptive_dataset_service.py` | Ensamblado de dataset de entrenamiento |
| `adaptive_retraining_service.py` | Gate de readiness + ejecución de retrain |
| `result_service.py` | Persistencia de resultados de partido |
| `slate_refresh_service.py` | Refresh orquestado de slate |
| `slate_discovery_service.py` | Descubrimiento de fixtures candidatos |
| `jornada_scoring_service.py` | Métricas de scoring por jornada |
| `history_import_service.py` | Wrapper de importación histórica |
| `availability_service.py` | Wrapper de disponibilidad de jugadores |
| `stats_service.py` | Wrapper de estadísticas |
| `evidence_service.py` | CRUD de evidencias |
| `source_service.py` | CRUD de fuentes |
| `entity_resolution_service.py` | Deduplicación de entidades |
| `progol_fixture_resolver.py` | Resolución de fixtures Progol |
| `artifact_storage.py` | I/O S3/local de artefactos |

### EXPERIMENTAL_NOT_WIRED
Presentes en el repo, accesibles por rutas o CLI, pero marcados como no-producción.

| Módulo | Estado |
|---|---|
| `neural_baseline_service.py` | `is_production=False`; rutas `/training/neural/*` solo para introspección |
| `expected_goals_service.py` | Solo CLI (`evaluate_xg`); no wired en rutas |
| `expected_goals_features.py` | Dependencia de expected_goals_service |

### Eliminados como dead code confirmado
- `narrative_extractor.py` — cero referencias de producción
- `stacking.py` — cero referencias de producción

---

## composition_hash y slate_version

`composition_hash` es un SHA-256 del listado ordenado de fixtures de una slate: `draw_code + home_team_id + away_team_id + competition_name`. Garantiza que cualquier cambio en la composición de partidos sea detectable.

`slate_version` se incrementa cada vez que el `composition_hash` cambia para el mismo `draw_code`. Snapshots de predicción, tickets y jornada scores se vinculan a `(slate_id, composition_hash)`.

**Regla crítica:** no modificar la lógica de cálculo de `composition_hash` sin una migración de schema y backfill controlado. Un cambio silencioso crearía una versión incompatible que invalidaría el historial de slates existentes sin aviso.

---

## Diagrama de servicios Docker

```
┌─────────────────────────────────────────────┐
│                docker-compose               │
│                                             │
│  ┌──────────┐    ┌──────────┐               │
│  │  proai   │    │  worker  │               │
│  │ FastAPI  │    │scheduler │               │
│  │ :8000    │    │ :8000    │               │
│  └────┬─────┘    └────┬─────┘               │
│       │               │                     │
│       └───────┬────────┘                    │
│               ▼                             │
│         ┌──────────┐                        │
│         │ postgres │                        │
│         │  :5432   │                        │
│         └──────────┘                        │
│                                             │
│  volumes: proai-data / proai-postgres-data  │
└─────────────────────────────────────────────┘
```
