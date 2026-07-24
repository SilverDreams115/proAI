# Release Checklist — Money Mode (R6.1)

Checklist to run **before playing** any quiniela. If a single item fails, the
default answer is **NO JUGAR** that slate. It is read-only: verifying changes nothing.

> Single command that covers most of the items:
> `docker compose exec --workdir /app/backend proai python -m scripts.operate_money_mode --active-upcoming`

---

## Pre-flight (infrastructure)

- [ ] `/api/ready` → `ready:true`, `database_ok:true`, `schema_up_to_date:true`
- [ ] `docker compose ps` → `proai` healthy
- [ ] worker healthy (or intentionally stopped during the review)
- [ ] `/api/slates` responds with the correct slates
- [ ] correct active slates (the ones you expect, with their `match_count`)
- [ ] no archived ones mixed into the active list

## Per-slate data

- [ ] predictions persisted **or** live available (`prediction_status`)
- [ ] `money_mode_validation` pass (no `data_blockers`)
- [ ] no stale metadata (no `placeholder_teams_*`, no `non_contiguous_positions`)

## Money Mode decision

- [ ] Money Mode produces one decision per slate (JUGAR / NO JUGAR, unambiguous)
- [ ] `NO SIMPLE` respected: no blocked position appears as a simple
- [ ] recommended ticket present **if** the decision is JUGAR
- [ ] main risks reviewed (`must_review_positions`)

## Safety / stability

- [ ] zero counts delta (`COUNTS_DELTA : ZERO` in the report, or SQL check)
- [ ] `write_safety.audit_passed = true`
- [ ] no unexpected writes (predictions / ticket / feature snapshots unchanged)
- [ ] stable UI: **Operational Money Mode Status** panel shows the correct state
- [ ] no slate auto-switch in the UI
- [ ] real ticket intact (snapshots unchanged)

## Final decision rules

- [ ] **If Money Mode says `NO JUGAR` → do NOT play.** (not relaxed for any reason)
- [ ] If JUGAR → use exactly the recommended ticket, respecting every `NO SIMPLE`
- [ ] No `NO SIMPLE` is converted into a simple
- [ ] No slate with stale metadata is played
- [ ] Live predictions are not trusted if `validation` blocks the slate

---

## Reference state (R6.1, at close)

| slate | decision | prediction | money_mode_ready |
|---|---|---|---|
| PG-2338 (weekend, 14) | **NO JUGAR** | persisted | yes |
| PGM-801 (midweek, 9) | **NO JUGAR** | live_available | yes |

`active_slate_count=2 · playable_slate_count=0 · blocked_slate_count=2`.

Both slates are low-evidence international friendlies; not even the maximum-coverage
ticket allowed covers the risk. The correct and honest decision is **not to play**.
