"""Post-backfill orchestrator (Fase 6.3).

After `bootstrap_football_data_sources` finishes, this script:

1. Trains a fresh XGBoost artifact on the whole historical dataset.
2. Runs the per-competition walk-forward evaluation so the new gate
   `live_pick_allowed` reflects the larger sample.
3. Publishes the auditable backtest under `reports/backtest_history/`.
4. Prints a per-competition summary so the operator can see at a glance
   which leagues now reach the `ready` threshold.

Usage (inside the container):
    python -m scripts.retrain_and_validate
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.model_training_service import ModelTrainingService

    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        service = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )

        print("[train] training new artifact...")
        artifact = service.train()
        print(
            f"[train] model_type={artifact.get('model_type')} "
            f"sample_size={artifact.get('training_sample_size')} "
            f"calibration_leagues={list((artifact.get('calibration_curves') or {}).keys())}"
        )

        print("[evaluate] running per-competition walk-forward...")
        report = service.evaluate_competitions_walk_forward()
        print(
            f"[evaluate] competitions_considered={report['competitions_considered']} "
            f"competitions_ready={report['competitions_ready']}"
        )
        print()
        print(
            f"{'competition':40s} | {'matches':>7s} | {'hit_rate':>9s} | "
            f"{'brier':>6s} | {'log_loss':>9s} | verdict"
        )
        print("-" * 100)
        competitions = report["competitions"]
        if isinstance(competitions, list):
            for entry in sorted(competitions, key=lambda c: c.get("matches_evaluated") or 0, reverse=True):
                if not isinstance(entry, dict):
                    continue
                evaluated = entry.get("matches_evaluated") or 0
                if evaluated < 10:
                    continue
                competition_key = entry.get("competition_key") or "?"
                hit = float(entry.get("hit_rate") or 0)
                brier = float(entry.get("brier_score") or 0)
                log_loss = float(entry.get("log_loss") or 0)
                verdict = entry.get("verdict") or "n/a"
                print(
                    f"{competition_key:40s} | {evaluated:7d} | {hit:9.3f} | "
                    f"{brier:6.3f} | {log_loss:9.3f} | {verdict}"
                )

        print()
        print("[publish] writing per-competition backtest history...")
        output_dir = Path("reports/backtest_history")
        index = service.publish_backtest_history(output_dir=output_dir)
        competitions_index = index.get("competitions", [])
        print(f"[publish] wrote {len(competitions_index)} files to {output_dir}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
