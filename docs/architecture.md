# proAI — Technical Architecture

## Overview

proAI is a sports-prediction platform for Progol quinielas. It ingests football statistics from structured sources, normalizes entities, generates `1/X/2` probabilities via a calibrated XGBoost model, and produces auditable tickets with risk coverage. All output is traceable: each prediction keeps its full feature map, its confidence band and its composition hash.

---

## End-to-end flow

```
External sources (TheSportsDB, football-data.org, football-data.co.uk)
        │
        ▼
[IngestionService]  ─── normalizes teams/competitions
        │               entity resolution, aliases, deduplication
        ▼
[DB: stats / results / evidence]
        │
        ▼
[FeatureService]  ─── recent-form window, H2H, goals, Elo
        │
        ▼
[ModelTrainingService / XGBoost artifact]
        │
        ▼
[PredictionService]  ─── 1/X/2 probabilities
        │               confidence band (blocked/low/medium/high)
        │               rationale + anchor gap diagnostic
        ▼
[TicketRecommendationService + TicketOptimizer]
        │               Simple / Dobles / Completa
        │               risk coverage (Poisson Binomial)
        ▼
[Active slate — PG-xxxx]
        │
        ▼ (after the match)
[ResultService / canonical_result]
        │
        ▼
[JornadaScoringService]  ─── hit-rate, Brier score
        │
        ▼
[AdaptiveDatasetService]  ─── audited training rows
        │
        ▼
[AdaptiveRetrainingService]  ─── readiness gate → retrain XGBoost
```

---

## Main components

### FastAPI (app/main.py)
Main server. Registers 20 routers under `/api/`. Auth via middleware (API key or signed session cookie). Worker and openapi-schema routes with an additional per-route guard.

### PostgreSQL
Main database. `SCHEMA_VERSION = 32`. Automatic migrations at startup (`app/db/migrations.py`), with Alembic as the audit trail (`backend/alembic/`). Never make schema changes outside this mechanism.

### Worker (app/workers/scheduler_worker.py)
Separate process. Runs scheduled jobs: ingestion refresh, archive/observe/auto-promote of the Progol pipeline. Controllable via `POST /api/worker/scheduler/run-once` (requires auth when credentials exist).

### IngestionService
Core of the data pipeline. Orchestrates connectors → parsers → normalization → entity resolution → persistence. 10+ services depend on it. **Do not modify without exhaustive tests.**

### PredictionService
Generates probabilities and confidence bands. Contains the loaded XGBoost model, the Poisson logic, the anchor computation, the rationale and the anchor gap diagnostic. **The most critical code in the system.**

### TicketOptimizer
Selects the optimal ticket given the picks and coverage parameters. Deterministic and audited. **Do not modify without demonstrating exact equivalence.**

### SchedulerService / CurrentProgolService
Manage the active slate's lifecycle: detect → observe → propose → auto-promote. Driver of the Progol flow.

### AdaptiveDataset + RetrainingGate
Accumulate results from complete jornadas as a training dataset. The gate evaluates readiness before allowing a real retrain. See `docs/ml_pipeline.md`.

### NeuralBaselineService
Experimental, offline. `is_production=False`. Not integrated into production predictions.

---

## Module classification

### DO_NOT_TOUCH_CRITICAL
Changes here without strong tests can silently break predictions, tickets or data integrity.

| Module | Reason |
|---|---|
| `prediction_service.py` | Probabilities, bands, rationale, persistence |
| `feature_service.py` | Feature engineering for the XGBoost model |
| `ticket_optimizer.py` | Ticket selection — audited determinism |
| `ticket_recommendation_service.py` | Coverage + recommendation |
| `ingestion_service.py` | Data pipeline — 10+ dependents |
| `model_training_service.py` | XGBoost train/eval/walk-forward |
| `model_training_artifacts.py` | Artifact I/O — critical serialization |
| `model_training_math.py` | Statistical foundations |
| `model_training_metrics.py` | Walk-forward metrics |
| `current_progol_service.py` | Active Progol context — worker driver |
| `slate_proposal_service.py` | observe→auto-promote pipeline |
| `normalization_service.py` | Canonical names — affects all data |
| `scheduler_service.py` | Scheduled jobs |
| `slate_service.py` | Core slate CRUD |
| `calibration.py` | PAV isotonic calibration |
| `drift.py` | PSI — drift detection |
| `coverage.py` | Poisson Binomial — ticket coverage |

### ACTIVE (wired auxiliaries)
Active and needed, but modifiable with lower risk if there are tests.

| Module | Role |
|---|---|
| `narrative_interpretation_service.py` | Text-signal extraction for evidence |
| `adaptive_dataset_service.py` | Training-dataset assembly |
| `adaptive_retraining_service.py` | Readiness gate + retrain execution |
| `result_service.py` | Match-result persistence |
| `slate_refresh_service.py` | Orchestrated slate refresh |
| `slate_discovery_service.py` | Candidate-fixture discovery |
| `jornada_scoring_service.py` | Per-jornada scoring metrics |
| `history_import_service.py` | Historical-import wrapper |
| `availability_service.py` | Player-availability wrapper |
| `stats_service.py` | Statistics wrapper |
| `evidence_service.py` | Evidence CRUD |
| `source_service.py` | Source CRUD |
| `entity_resolution_service.py` | Entity deduplication |
| `progol_fixture_resolver.py` | Progol fixture resolution |
| `placeholder_fixtures.py` | Fabricated-fixture formula, ladder detection, cierre rebase |
| `artifact_storage.py` | S3/local artifact I/O |

### EXPERIMENTAL_NOT_WIRED
Present in the repo, accessible via routes or CLI, but marked as non-production.

| Module | State |
|---|---|
| `neural_baseline_service.py` | `is_production=False`; `/training/neural/*` routes for introspection only |
| `expected_goals_service.py` | CLI only (`evaluate_xg`); not wired into routes |
| `expected_goals_features.py` | Dependency of expected_goals_service |

### Removed as confirmed dead code
- `narrative_extractor.py` — zero production references
- `stacking.py` — zero production references

---

## Fabricated fixtures (`is_placeholder`)

The LN guide lists pairs, not fixtures. When `ProgolFixtureResolver` finds no
ingested match for a pair, promotion still has to produce 9 or 14 positions, so
it fabricates one: kickoff at `cierre + 12h` stepped an hour per position, with a
competition inferred from team history. That construction is legitimate — the
slate must be complete — but it is not an observation, and `matches.is_placeholder`
is what records the difference.

Three rules depend on that flag:

* **The UI never prints a fabricated kickoff as the real one.** `SlateMatchResponse`
  exposes it as `kickoff_estimated`; the pick card shows the day plus "horario por
  confirmar" instead of an hour nobody published.
* **`find_upcoming_match_for_pair` excludes placeholder rows.** Otherwise the next
  slate for the same pair adopts a previous slate's invention as a real match and
  copies its kickoff forward.
* **Correcting a cierre rebases the kickoffs derived from it** — see
  `placeholder_fixtures.rebase_to_cierre`. Only a fabricated ladder moves; a slate
  backed by ingested fixtures forms no ladder and is never rewritten.

A real fixture always outranks a fabricated one: a payload backed by a feed clears
the flag, and a fabricated payload never re-applies it to a row a feed has since
confirmed. Same one-way rule the team and competition placeholder flags use.

---

## composition_hash and slate_version

`composition_hash` is a SHA-256 of the ordered fixture list of a slate: `draw_code + home_team_id + away_team_id + competition_name`. It guarantees that any change in the match composition is detectable.

`slate_version` is incremented every time the `composition_hash` changes for the same `draw_code`. Prediction snapshots, tickets and jornada scores are linked to `(slate_id, composition_hash)`.

**Critical rule:** do not modify the `composition_hash` computation logic without a schema migration and a controlled backfill. A silent change would create an incompatible version that would invalidate the history of existing slates without warning.

---

## Docker services diagram

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
