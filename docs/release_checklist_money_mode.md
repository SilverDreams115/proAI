# Release Checklist — Money Mode (R6.1)

Checklist a correr **antes de jugar** cualquier quiniela. Si un solo ítem falla, la
respuesta por defecto es **NO JUGAR** esa slate. Es read-only: verificar no cambia nada.

> Comando único que cubre la mayoría de los ítems:
> `docker compose exec --workdir /app/backend proai python -m scripts.operate_money_mode --active-upcoming`

---

## Pre-flight (infraestructura)

- [ ] `/api/ready` → `ready:true`, `database_ok:true`, `schema_up_to_date:true`
- [ ] `docker compose ps` → `proai` healthy
- [ ] worker healthy (o detenido a propósito durante la revisión)
- [ ] `/api/slates` responde con las slates correctas
- [ ] slates activas correctas (las que esperas, con su `match_count`)
- [ ] no archivadas mezcladas en la lista activa

## Datos por slate

- [ ] predictions persistidas **o** live disponibles (`prediction_status`)
- [ ] `money_mode_validation` pass (sin `data_blockers`)
- [ ] no stale metadata (sin `placeholder_teams_*`, sin `non_contiguous_positions`)
- [ ] `ticket_canary_dry_run` corre sin error

## Decisión Money Mode

- [ ] Money Mode genera una decisión por slate (JUGAR / NO JUGAR, sin ambigüedad)
- [ ] `NO SIMPLE` respetado: ninguna posición bloqueada aparece como simple
- [ ] boleto recomendado presente **si** la decisión es JUGAR
- [ ] riesgos principales revisados (`must_review_positions`)

## Seguridad / estabilidad

- [ ] counts delta cero (`COUNTS_DELTA : ZERO` en el reporte, o verificación SQL)
- [ ] `write_safety.audit_passed = true`
- [ ] sin escrituras inesperadas (predictions / ticket / feature snapshots sin cambios)
- [ ] UI estable: panel **Operational Money Mode Status** muestra el estado correcto
- [ ] sin auto-switch de slate en la UI
- [ ] ticket real intacto (snapshots sin cambios)

## Reglas de decisión finales

- [ ] **Si Money Mode dice `NO JUGAR` → NO se juega.** (no se relaja por ningún motivo)
- [ ] Si JUGAR → se usa exactamente el boleto recomendado, respetando todos los `NO SIMPLE`
- [ ] Ningún `NO SIMPLE` se convierte en simple
- [ ] Ninguna slate con metadata stale se juega
- [ ] No se confía en predicciones live si `validation` bloquea la slate

---

## Estado de referencia (R6.1, al cierre)

| slate | decisión | predicción | money_mode_ready |
|---|---|---|---|
| PG-2338 (weekend, 14) | **NO JUGAR** | persisted | sí |
| PGM-801 (midweek, 9) | **NO JUGAR** | live_available | sí |

`active_slate_count=2 · playable_slate_count=0 · blocked_slate_count=2`.

Ambas slates son amistosos internacionales de baja evidencia; ni el boleto de máxima
cobertura permitido cubre el riesgo. La decisión correcta y honesta es **no jugar**.
