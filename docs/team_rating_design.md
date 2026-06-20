# Productive Team Rating Design (proAI)

Status: **DESIGN ONLY — nothing persisted, no migration applied, no model change.**
Companion to the read-only prototype `backend/scripts/audit_team_rating_coverage.py`
(commit `385cb9f`). This document proposes how to make the internal Elo rating
productive *later*; it does not implement any of it.

## 0. Why

PG-2338 and the historical audit showed the bottleneck is **data, not weights**:
`fallback_rate=1.00`, `usable_model=0/14`, `evidence_count=0`, H2H ~null, and
no strength feature exists at all (`home_advantage` is a constant 1.0). The Elo
prototype proved a strength signal is recoverable *today* from `match_results`.

### Audit baseline (read-only, current DB)

| metric | value |
|---|---|
| total_results | 15150 |
| rated_matches_used (conflicts excluded) | 14073 |
| distinct_matches_with_results | 14093 |
| teams_with_rating | 728 (national 304 / club 424) |
| teams_without_rating | 35 |
| confidence buckets | strong 506 · medium 97 · weak 125 · no_rating 0 |
| PG-2338 both_medium_plus | 13/14 (only pos13 Rep. Congo missing) |
| International Friendlies both_medium_plus_rate | 0.931 (72 matches) |
| Copa Libertadores both_medium_plus_rate | 0.964 (28 matches) |
| Brasileirao both_medium_plus_rate | 0.750 (4 matches, control) |

## 1. Persistence model (proposed tables — NOT migrated)

Decision: **immutable run + per-run snapshot + a thin "current" pointer.**
Rationale: ratings are recomputed from scratch each run (single deterministic
pass), so a full snapshot per run is cheap, fully reproducible and auditable.
Avoid an in-place "current" table that loses history; instead keep all runs and
mark one as `active`. Reads use the latest `active` run.

### `team_rating_runs` (one row per recompute)
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| algorithm_version | varchar(32) | e.g. `elo_v1` |
| config_json | text | full `EloParams` + filters (frozen, reproducible) |
| source_result_count | int | rows considered |
| rated_match_count | int | matches actually applied |
| excluded_match_count | int | conflicts / no-score / sign-only |
| input_checksum | varchar(64) | sha256 of the ordered input tuples → reproducibility proof |
| output_checksum | varchar(64) | sha256 of sorted (team_id,rating) → detects drift |
| status | varchar(16) | `computed` / `active` / `superseded` |
| created_at | timestamptz | |

### `team_rating_snapshots` (one row per team per run)
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| run_id | fk → team_rating_runs | |
| team_id | fk → teams | |
| namespace | varchar(16) | `club` / `national` / `unknown` |
| rating | float | |
| rating_delta | float | last update delta (debug) |
| matches_count | int | |
| wins / draws / losses | int | |
| goals_for / goals_against | int | |
| confidence_bucket | varchar(16) | `no_rating`/`weak`/`medium`/`strong` |
| last_result_at | timestamptz | |
| competitions_seen_json | text | |

Unique: `(run_id, team_id)`. Index: `(team_id, run_id)` for latest-active reads.

`TeamRatingConfig` and `TeamRatingInputMatch` are **not** persisted as tables —
config lives in `team_rating_runs.config_json`; the input match list is derived
deterministically from `match_results` + `CanonicalResultRepository` and pinned
by `input_checksum` (no need to duplicate 14k rows per run).

**Snapshot-per-run vs current+history:** snapshot-per-run chosen. A `current`
table would need its own history table anyway; the run model gives both for free
and makes "which rating produced this prediction" answerable via `run_id`
(mirrors how `composition_hash` pins slate identity — see
`docs/` hash-contract work).

## 2. Namespace club/national

### Classification rule (today's data)
Per team, collect the competition names of its rated matches. A name is
"national" if it contains any of: `friendl, amistos, qualif, world cup, copa
america, euro, nations league, international, concacaf, gold cup, afcon, africa
cup, asian cup, conmebol`.

```
national_fraction = national_comp_matches / total_comp_matches
namespace = national   if national_fraction >= 0.5
          = club       if national_fraction == 0  (or < 0.5)
          = unknown    if the team has no competitions / no results
```

### Sources audited
- `competition.name` — **primary, reliable** (keyword fingerprint). Used above.
- `team.country` — present but a country string also exists for clubs (their
  home country); **cannot** distinguish "Brazil (NT)" from a Brazilian club.
- team metadata — no `is_national` flag exists (would need a schema add).
- matches/competitions join — same signal as `competition.name`.

### Failure modes
- A national team that played a club in a one-off exhibition tagged under a club
  competition could be mis-bucketed if that comp dominates its history (rare).
- New keyword-less national competitions (e.g. a regional cup) misread as club.
- Clubs in tournaments named with "international" (e.g. some club intercontinental
  cups) could be mis-tagged national → mitigate by requiring NT-specific keywords
  (`world cup`, `nations league`, `friendl`) to outweigh generic `international`.

### Blocking `unknown`
- `unknown` (no rated matches) → **never** emit a rating; treat as `no_rating`.
- Productive feature must check `namespace != unknown` before trusting a rating.

### Cross-namespace matches
- Compute **two separate Elo pools** (`club`, `national`) in `elo_v1`. A match
  whose two teams resolve to different namespaces is **excluded** from both pools
  (logged in `excluded_match_count`). These are rare (club-vs-NT exhibitions) and
  would corrupt the zero-sum invariant across pools.

## 3. Elo v1 config (proposed productive defaults)

```
algorithm_version = "elo_v1"
initial_rating    = 1500
k_base            = 32
home_advantage    = 0.0          # neutral; revisit per-namespace after backtest
goal_diff_enabled = false        # 5-0 must not move 5x a 1-0 on thin samples
goal_diff_cap     = 1.75         # only if enabled
recency_decay     = false        # report only until backtest justifies
min_matches_for_confident_rating = 5
namespaces_separated = true
include_friendlies = true        # they ARE the Progol bulk; excluding them
                                 # would zero-out the very slates we need
include_sign_only  = false       # no score → cannot update Elo
exclude_conflicts  = true        # via CanonicalResultRepository
score_required     = true
ordering           = played_at asc, match_id asc
```

### Should `K` vary?
- **competition_type / friendly vs official**: YES, eventually. Friendlies carry
  rotation noise → a lower K (e.g. 24) for friendlies, 32 for official would
  reduce churn. **Defer to `elo_v2`** — keep `elo_v1` single-K for a clean
  baseline to backtest against.
- **club vs national**: handled by separate pools, not by K.
- **recency**: address via decay (off in v1), not via K.
- **confidence**: do NOT vary K by confidence — confidence is an *output*; let
  the feature layer down-weight low-confidence ratings, not the update rule.

Keep `elo_v1` deliberately simple so the backtest measures the *signal*, not a
pile of tuning knobs.

## 4. Feature design (future — NOT wired into FeatureService)

Proposed features once a rating run is `active`:

| feature | definition |
|---|---|
| `home_rating`, `away_rating` | latest active snapshot rating; default `initial_rating` if missing |
| `rating_diff` | `home_rating - away_rating` (the core strength signal) |
| `home_rating_confidence`, `away_rating_confidence` | bucket → ordinal 0..3 |
| `both_rating_medium_plus` | both `matches_count >= 4` |
| `rating_namespace` | `club` / `national` (must match both teams) |
| `rating_match_count_diff` | `home_matches_count - away_matches_count` (asymmetry signal) |

### Missing-rating behaviour
- `no_rating` (0 matches) → **do not default to 1500 silently as a real signal.**
  Emit `rating_present=false`; `rating_diff` contributes **only** when both sides
  have a rating. A neutral 1500 default is fine for arithmetic but must be paired
  with a `both_rating_medium_plus=false` flag so the model/sanity layer can
  discount it. **No_rating must NOT unblock a match by itself.**
- `weak` (1–3) → counts as present but **lowers evidence_level** (treat like a
  thin anchor; do not let it promote to FIJO/LISTO on its own).
- Rating is **additive context**, never a hard block on its own; the existing
  `_has_insufficient_data` gate stays authoritative until the backtest says
  rating can substitute for recent-form/H2H anchors.

## 5. Approval gate + backtest protocol

Open the `approval_gate` per competition only after an **offline** backtest.

### Competitions (priority)
1. **Copa Libertadores** — 96% rating coverage, 22 learning-ready, already has
   results: best first candidate.
2. **International Friendlies** — 93% rating coverage but results pending; the
   Progol bulk, highest payoff, highest risk.
3. **Brasileirao** — control (already xgboost-approved): rating must not regress it.

### Mandatory metrics (with-rating vs baseline-heuristic vs current-xgboost)
- top1 accuracy, top2 coverage
- Brier score, log loss
- calibration bins (reliability curve)
- fallback_rate, usable_model_rate
- accuracy by competition, by evidence_level
- **accuracy with vs without the rating feature** (ablation)

### Minimum acceptance criteria
- With-rating model **beats baseline heuristic** on Brier AND log loss on the
  competition's holdout.
- Does **not** regress Brier/log loss on Brasileirao control.
- Calibration: |observed − predicted| within tolerance across bins (no
  systematic over/under-confidence).
- Sample size: ≥ the existing `XGBOOST_MIN_SAMPLE_SIZE` (30) prior official
  matches per competition; friendlies need ≥ N completed friendlies before the
  gate opens.
- Coverage: ≥ ~80% of the competition's matches have `both_rating_medium_plus`.

Until then: gate stays closed → engine stays heuristic → behaviour unchanged.

## 6. Phased plan

| phase | scope | DB | model |
|---|---|---|---|
| **R1** | rating domain module + pure Elo calculator + unit tests | none | none |
| **R2** | migration **draft** + repository + `compute --dry-run` / `--apply` (confirm-gated) | writes only on explicit `--apply` | none |
| **R3** | `FeatureService` can READ latest active snapshot; coverage audit *with* rating; **not used in prediction** | read | none |
| **R4** | offline experimental train: baseline vs rating-feature ablation backtest | read | experimental only, not active |
| **R5** | controlled activation: per-competition approval gate, sanity thresholds, UI confidence flags | active run | activate per gate |

Each phase is independently committable and reversible; R2's `--apply` mirrors
the relink tooling's double-confirmation pattern (`--apply --confirm <token>`).

## 7. Risks / blockers

### must_block (cannot productivize until resolved)
- **club/national mixing** — separate pools mandatory; cross-namespace matches
  excluded. A mixed pool produces meaningless diffs.
- **placeholder teams** — must never receive a rating (e.g. PG-2338 pos13 Rep.
  Congo, 0 results). Filter `is_placeholder=false` + `matches_count>0`.
- **sign-only / scoreless results** — cannot update Elo; must be excluded
  (`score_required=true`).
- **result conflicts** — excluded via `CanonicalResultRepository` (20 matches).

### should_fix (degrade quality; fix before/with R4)
- **friendlies rotation noise** — lower-K-for-friendlies (`elo_v2`) or evidence
  down-weight.
- **old ratings without recency** — idle teams keep stale ratings; add decay and
  surface `last_result_at` staleness.
- **small per-competition sample** — gate on sample size; do not approve thin
  competitions.
- **new teams / weak (1–3 matches)** — treat as low-confidence; never authoritative.

### acceptable (monitor, not blocking)
- single global namespace-internal disconnection between isolated leagues —
  acceptable for a *relative* within-pool signal; rating_diff is comparative.
- minor namespace misclassification on keyword-less comps — low volume.

## 8. Recommendation

**Advance to R1** (pure rating domain module + tests, no DB). The audit proves the
signal is recoverable and high-coverage; R1 is fully reversible, touches no DB,
no model, no schema, and produces the deterministic, checksummed calculator that
every later phase depends on. Hold R2+ (persistence, feature, backtest,
activation) for explicit per-phase authorization. Do **not** touch weights,
calibration, the approval gate, or PG-2338 data until R4's backtest justifies it.
