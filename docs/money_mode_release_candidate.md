# Money Mode Release Candidate (R6.0)

> **Historical record.** This report describes the run as observed on the date
> below. The team-rating canary and the ticket canary dry-run it references have
> since been removed in full, together with the `team_rating_*` tables; the
> canary sections and those two table counts no longer correspond to anything in
> the codebase. Everything else (decision, tickets, write-safety) still holds.

**Date/time:** 2026-06-24 04:30 UTC
**HEAD (pre-commit):** `2a0a22a` — _Add ticket canary dry-run_
**Branch:** `chore/production-polish`
**Mode:** `money_mode_release_candidate` · **read-only** · full activation OFF · ticket
integration OFF · productive optimizer OFF.

Money Mode is the final operational layer before using the system for real-money
decisions in Progol. For each active/upcoming slate it answers a single question —
**play / don't play** — and builds **in memory** three tickets (aggressive, balanced,
conservative) with per-match justification. It does not activate or change the real ticket and
does not write any row.

---

## Baseline counts (worker stopped)

| table | baseline | final | delta |
|---|---:|---:|---:|
| match_results | 15150 | 15150 | 0 |
| predictions | 2177 | 2177 | 0 |
| matches | 14230 | 14230 | 0 |
| progol_slate_matches | 113 | 113 | 0 |
| match_feature_snapshots | 1124 | 1124 | 0 |
| ticket_recommendation_snapshots | 162 | 162 | 0 |
| team_rating_runs | 1 | 1 | 0 |
| team_rating_snapshots | 729 | 729 | 0 |
| model_training_runs | 28 | 28 | 0 |
| progol_slates | 10 | 10 | 0 |

**Zero delta** after building Money Mode for both slates and repeating the endpoints
5× with the worker stopped. No GET wrote predictions, feature snapshots or
ticket snapshots.

---

## Money Mode policy

- **Scope:** `active_upcoming`. Today it covers **PG-2338** (weekend) + **PGM-801** (midweek);
  any future slate enters automatically under the same rule (`active_slate_scope`).
- **Canary:** on canary-active positions the `effective_decision_probabilities` are
  consumed; the rest uses the current decision/display view.
- **Guardrail (authoritative):** a position with `presentation_guard.simple_allowed=false`
  **never** appears as a simple. A fixed pick forced onto a NO SIMPLE position is reported
  as `no_simple` (uncovered risk), so that a `no_dejar_simple` /
  `risk_high` / `review` / `blocked` match is never read as fixed.
- **Tickets = optimizer modes** (respect the Progol ticket rules):
  - **agresivo** = `simple` mode (more fixed picks, cheaper).
  - **balanceado** = `doubles` mode (bounded doubles plan, recommended by default).
  - **conservador** = `full` mode (maximum doubles+triples coverage allowed).
- **Cost:** there is no per-combination tariff configured in the system →
  `estimated_cost = null` and it is documented. `estimated_combinations` is reported
  (product of 1 simple · 2 double · 3 triple).
- **Decision rule:** if even the **conservative** ticket leaves more than 34% of the
  positions NO SIMPLE as a forced fixed pick → **NO JUGAR** (risk not coverable). If the
  balanced one covers all NO SIMPLE → JUGAR BALANCEADO. Intermediate cases →
  JUGAR SOLO CONSERVADOR.

---

## PG-2338 · weekend · 14 matches

- **DECISION: `NO JUGAR`** (confidence: cautious) — recommended ticket: **none**.
- **Reason:** too many NO SIMPLE with no possible coverage: **6/14** positions remain
  as a forced fixed pick even in the conservative ticket (maximum coverage allowed by
  the ticket rules). The risk is not coverable and no mode reaches the coverage
  target.
- **Predictions:** persisted. No data blockers.

| ticket | S / NS / D / T | combinations | cost | E[hits] | jackpot | risk | covers NO SIMPLE |
|---|---|---:|---|---:|---:|---|---|
| agresivo | 0 / 14 / 0 / 0 | 1 | n/a | 6.91 | 0.0000 | very_high | no (14 unc.) |
| balanceado | 0 / 6 / 8 / 0 | 256 | n/a | 9.49 | 0.0040 | very_high | no (4,6,7,12,13,14) |
| conservador | 0 / 6 / 4 / 4 | 1296 | n/a | 10.63 | 0.0153 | very_high | no (4,6,7,12,13,14) |

- **NO SIMPLE matches:** **all (1–14)** — they are international friendlies with
  low evidence / suspicious class / blocked.
- **Mandatory review:** 1,3,4,5,6,7,8,9,10,11,12,13,14.
- **Canary influences:** 1,2,3,5,8,11.
- **Main risk:** not even the maximum-coverage ticket covers the risk; 6 matches
  remain as a forced coin-flip on the most likely pick and the coverage target is not
  met in any mode.

## PGM-801 · midweek · 9 matches (live prediction)

- **DECISION: `NO JUGAR`** (confidence: cautious) — recommended ticket: **none**.
- **Reason:** too many NO SIMPLE with no possible coverage: **4/9** positions remain
  as a forced fixed pick even in the conservative ticket.
- **Predictions:** live (no persisted ticket) — Money Mode computed live.
  Warning: `live_predictions_only`. No data blockers.

| ticket | S / NS / D / T | combinations | cost | E[hits] | jackpot | risk | covers NO SIMPLE |
|---|---|---:|---|---:|---:|---|---|
| agresivo | 0 / 9 / 0 / 0 | 1 | n/a | 4.11 | 0.0007 | very_high | no (1–9 unc.) |
| balanceado | 0 / 6 / 3 / 0 | 8 | n/a | 5.12 | 0.0048 | very_high | no (2,4,5,6,7,9) |
| conservador | 0 / 4 / 3 / 2 | 72 | n/a | 6.34 | 0.0296 | very_high | no (4,5,6,7) |

- **NO SIMPLE matches:** **all (1–9)**.
- **Mandatory review:** 1,4,5,7,8,9.
- **Canary influences:** 1,2,3,5,8.
- **Main risk:** same pattern as PG-2338 — a low-evidence friendlies slate;
  even with maximum coverage 4 forced coin-flips remain.

---

## Active-upcoming summary

- **2** slates in scope (`active_upcoming`): PG-2338 + PGM-801.
- **0** playable slates (`playable_slate_count = 0`): both → **NO JUGAR**.
- Future slates inherit the policy automatically.

## Norway vs France (guardrail case)

- PG-2338 pos 7 / PGM-801 pos 6: **NO SIMPLE** in both. The `money_mode_pick` is
  coverage (V/E double in the conservative plan), never a fixed pick. Reasons:
  `risk_high`, `no_dejar_simple`, `suspicious_class`. The guardrail is respected across the
  three tickets and in the UI.

---

## No-write validation

- `write_safety = { writes_performed: false, snapshots_created: false }` in all
  responses (service, endpoint, CLI).
- Transaction marked `SET TRANSACTION READ ONLY` and always with rollback (helper
  `read_only_transaction`).
- Endpoints repeated 5× with the worker stopped → **zero counts delta** (table above).
- PG-2338 real ticket intact (snapshots = 162, no changes).

## Conclusion

> **PG-2338 → NO JUGAR. PGM-801 → NO JUGAR.**

The system produces actionable output and the honest decision is **not to play either
of the two slates**: they are international-friendlies jornadas with low evidence where
all positions are marked NO SIMPLE, and not even the maximum-coverage ticket
allowed by the Progol rules covers the risk (6/14 and 4/9 forced coin-flips,
coverage target not met). Money Mode protects the money: it does not turn any
dangerous signal into a simple and does not recommend a ticket that fails to cover the main risk.

---

## Constraints respected

no full activation · no training · no productive optimizer · no real ticket integration
· no ticket snapshot writes · no prediction writes · no match_feature_snapshot
writes · no changes to persisted recommendations · no results apply · no API-Football
online · no schema changes / migrations · no deletes/reverts · no push/remote without
authorization · no secrets · canary not expanded beyond `active_upcoming` · NO SIMPLE
guardrail respected · real ticket intact.
