# Team Rating — Activation Protocol (R5)

Status: **PLAN ONLY. Nothing in R5 is implemented or active.** This document
is the gated checklist for turning the prepared rating (R2–R4) into a
productive feature, one competition at a time. Until every gate below is
green for a competition, the engine stays heuristic and predictions are
unchanged.

Companion docs: `docs/team_rating_design.md` (design), the R2–R4 code
(`app/models/team_rating.py`, `app/repositories/team_rating_repository.py`,
`backend/scripts/compute_team_ratings.py`,
`app/services/team_rating_feature_service.py`,
`backend/scripts/backtest_rating_feature_plan.py`) and the migration draft
`backend/alembic/drafts/0019_team_rating_persistence.py`.

## What is prepared vs what is NOT active

| Prepared (R2–R4) | Explicitly NOT active |
|---|---|
| Migration draft (inert, outside `versions/`) | No schema applied to any real DB |
| SQLAlchemy models + repository | No tables created in production |
| `compute --dry-run` (read-only) | `--apply` never run |
| Read-only feature helper behind `PROAI_TEAM_RATING_FEATURE_ENABLED` (default OFF) | FeatureService / PredictionService untouched |
| Backtest planning harness (read-only) | No training, no calibration, no artifacts |
| — | Approval gate unchanged; no probability/prediction change |

## Activation sequence (each step gated, manual, reversible)

1. **R2 apply — persist a run.**
   - Move `backend/alembic/drafts/0019_team_rating_persistence.py` into
     `backend/alembic/versions/`, bump `SCHEMA_VERSION` to 19 in
     `app/db/migrations.py`, add `_migrate_to_v19` (CREATE TABLE IF NOT
     EXISTS, like `_migrate_to_v17`) wired into both
     `_run_migrations_unlocked` and `_bootstrap_schema`. This is what makes
     the schema real.
   - Run `compute_team_ratings.py --apply --confirm COMPUTE-TEAM-RATINGS-V1`.
     The CLI aborts unless: exact token, tables exist, no active run with the
     same `input_checksum`, no incompatible active run. It supersedes the
     previous active run and marks the new one `active`.

2. **R3 feature read-only audit.**
   - With `PROAI_TEAM_RATING_FEATURE_ENABLED=true` in a NON-production
     environment, audit `load_rating_features` coverage against the latest
     active run. Confirm `rating_present` / `both_rating_medium_plus` behave
     per spec. Still NOT wired into FeatureService.

3. **R4 experimental offline training.**
   - Offline only. Train an experimental model WITH the rating feature vs the
     baseline heuristic vs current xgboost. No artifact is promoted.

4. **Backtest with vs without rating (ablation).**
   - Mandatory metrics: top1 accuracy, top2 coverage, Brier, log loss,
     calibration bins, fallback_rate, usable_model_rate, accuracy by
     competition and by evidence_level, and the with/without-rating ablation.

5. **Open the approval gate per competition** — only if metrics pass (below).

6. **Controlled activation** — enable the feature for the approved
   competition only, keep the sanity layer authoritative, surface confidence
   flags in the UI.

## Minimum acceptance criteria (per competition)

- **Sample size**: ≥ `XGBOOST_MIN_SAMPLE_SIZE` (30) completed official
  matches; friendlies need ≥ N completed friendlies before the gate opens.
- **Brier improves** vs the baseline heuristic on the holdout.
- **Log loss does not worsen**.
- **Calibration acceptable**: |observed − predicted| within tolerance across
  bins (no systematic over/under-confidence).
- **fallback_rate drops** without inflating false confidence (no FIJO/LISTO
  on low evidence).
- **Brasileirao (control) does not regress** on Brier/log loss.
- **Coverage** ≥ ~80% of the competition's matches have
  `both_rating_medium_plus`.
- **Sanity layer keeps blocking** low-evidence picks (rating is additive
  context, never a standalone unblock).

Priority candidates (design §5): **Copa Libertadores** (high coverage, has
results) → **International Friendlies** (Progol bulk, highest payoff/risk) →
**Brasileirao** (control). The harness
(`backtest_rating_feature_plan.py`) reports which competitions currently meet
the learning-ready + coverage bars.

Until a competition clears every gate: **gate stays closed → engine stays
heuristic → behaviour unchanged.**
