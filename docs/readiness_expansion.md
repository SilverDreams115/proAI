# Readiness Expansion (R6.3)

Cómo aumentar los partidos en **READY** *sin falsear confianza*. Esta es una
herramienta de **auditoría read-only**: explica por qué cada partido no está
READY y qué dato real lo desbloquearía. **No cambia estados, no baja umbrales,
no oculta LOW_EVIDENCE y no convierte amistosos sin evidencia en READY.**

## Definición operativa de READY

Un partido está **READY** cuando el *presentation guard* permite un pick simple
defendible (sin blockers de riesgo/evidencia). `safe_to_promote_now=true` solo
cuando la evidencia **ya** es suficiente — la auditoría nunca inventa un READY.

## Cómo correr la auditoría

```bash
docker compose exec --workdir /app/backend proai \
  python -m scripts.audit_ready_expansion --draw-code PG-2338
... --draw-code PGM-801
... --active-upcoming
... --active-upcoming --json
```

Por partido reporta: `position`, `match`, `current_status`, `blocked_by`,
`can_be_improved_by`, `safe_to_promote_now`. Por slate: `ready_now`,
`ready_potential_with_external_data`, `ready_potential_after_provider_results`,
`safe_promotions`, `no_promote_reason`.

## Categorías de blocker

`low_evidence`, `fallback_used`, `suspicious_class`, `stale_metadata`,
`friendly_context`, `placeholder_team`, `provider_unmatched`,
`canary_not_active`, `no_result_history`, `partial_rating`, `no_rating`,
`calibrator_missing`.

## Qué desbloquea cada cosa (`can_be_improved_by`)

| blocker | dato real que lo mejora |
|---|---|
| low_evidence | más historial de resultados de los equipos |
| fallback_used | rating disponible para ambos equipos |
| suspicious_class | mejor calibración |
| stale_metadata | corregir metadata/mapping |
| friendly_context | calibrador específico de amistosos |
| provider_unmatched | resultado finalizado del proveedor (dry-run) |
| placeholder_team | resolver el equipo del fixture |

## Cómo mejorar READY **sin falsear confianza**

Permitido (cambios seguros, solo si hay evidencia real):
- arreglar **mapping** de equipos/fixtures,
- arreglar **metadata stale**,
- usar **ratings existentes** cuando están disponibles para ambos equipos,
- usar el **provider dry-run** como evidencia secundaria de resultado.

Prohibido:
- bajar `min_evidence` o cualquier umbral solo para ver más READY,
- ignorar `fallback`/`LOW_EVIDENCE`,
- pasar amistosos sin evidencia a READY por defecto,
- quitar `risk_high` o relajar `NO SIMPLE`.

## Estado actual (R6.3)

Para **PG-2338** y **PGM-801** (amistosos internacionales, baja evidencia):

> **No hay promociones READY seguras en esta fase.**

Cada partido sigue con evidencia insuficiente, fallback o contexto de amistoso.
El proveedor gratuito no cubre estas competencias, así que tampoco aporta
resultados finalizados como evidencia secundaria. Promover sin datos reales
falsearía la confianza, por lo que `safe_promotions = 0`. La auditoría deja
explícito qué dato concreto desbloquearía cada partido cuando exista.
