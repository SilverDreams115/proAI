from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.settings import settings
from app.db import session as db_session
from app.db.migrations import migration_audit_errors
from app.db.migrations import run_migrations
from app.models import tables  # noqa: F401
from app.repositories.entity_repository import EntityRepository
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.scheduler_repository import SchedulerRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.repositories.training_repository import TrainingRepository
from app.services.current_progol_service import CurrentProgolService
from app.services.model_training_service import ModelTrainingService
from app.services.scheduler_service import SchedulerService


def _print_json(payload: Any) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _print_backtest_progress(event: dict[str, Any]) -> None:
    name = event.get("competition_name") or event.get("competition_key")
    if event["event"] == "start":
        print(
            f"publish-backtest: starting {event['total_competitions']} competitions -> {event['output_dir']}",
            file=sys.stderr,
            flush=True,
        )
    elif event["event"] == "competition_start":
        print(
            "publish-backtest: "
            f"[{event['position']}/{event['total_competitions']}] {name} "
            f"({event['matches']} matches)",
            file=sys.stderr,
            flush=True,
        )
    elif event["event"] == "competition_done":
        print(
            "publish-backtest: "
            f"[{event['position']}/{event['total_competitions']}] {name} done "
            f"({event['matches_evaluated']} evaluated, {event['file']})",
            file=sys.stderr,
            flush=True,
        )
    elif event["event"] == "done":
        print(
            f"publish-backtest: wrote index {event['index_file']}",
            file=sys.stderr,
            flush=True,
        )


def refresh_current(args: argparse.Namespace) -> None:
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        response = CurrentProgolService(
            SourceRepository(session),
            IngestionRepository(session),
            SlateRepository(session),
        ).refresh_current(local_path=args.local_path)
        _print_json(response)
    finally:
        session.close()


def ensure_current_job(args: argparse.Namespace) -> None:
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        ingestion_repository = IngestionRepository(session)
        source = CurrentProgolService(
            SourceRepository(session),
            ingestion_repository,
            SlateRepository(session),
        ).ensure_default_context_source()
        job = SchedulerService(SchedulerRepository(session), ingestion_repository).ensure_current_progol_refresh_job(
            source_id=source.id,
            interval_minutes=args.interval_minutes,
            job_name=args.job_name,
        )
        _print_json(
            {
                "id": job.id,
                "job_name": job.job_name,
                "source_id": job.source_id,
                "interval_minutes": job.interval_minutes,
                "is_active": job.is_active,
                "next_run_at": job.next_run_at,
            }
        )
    finally:
        session.close()


def evaluate(args: argparse.Namespace) -> None:
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        service = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        if args.mode == "competitions":
            payload = service.evaluate_competitions_walk_forward(
                min_training_matches=args.min_training_matches,
                confidence_threshold=args.confidence_threshold,
            )
        elif args.mode == "calibration":
            payload = service.calibration_report(
                min_training_matches=args.min_training_matches,
                confidence_threshold=args.confidence_threshold,
            )
        else:
            payload = service.evaluate_walk_forward(
                min_training_matches=args.min_training_matches,
                confidence_threshold=args.confidence_threshold,
            )
        _print_json(payload)
    finally:
        session.close()


def publish_backtest(args: argparse.Namespace) -> None:
    from pathlib import Path

    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        service = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        index = service.publish_backtest_history(
            output_dir=Path(args.output_dir),
            min_training_matches=args.min_training_matches,
            progress=None if args.no_progress else _print_backtest_progress,
        )
        _print_json(index)
    finally:
        session.close()


def evaluate_xg(args: argparse.Namespace) -> None:
    """Walk-forward evaluation of the Sprint 7.1 Expected Goals model.

    Splits the historical match table at `--train-fraction` (time-based,
    no shuffling so the eval fold strictly post-dates the train fold),
    fits the xG booster on the train fold, and reports RMSE / MAE vs
    the per-competition baseline on the held-out tail. Prints a
    per-competition table so the operator can see which leagues the
    booster actually beats.

    Nothing is wired into production scoring by this command — it only
    surfaces the numbers. Integration is gated on these results.
    """
    from app.services import expected_goals_service as egs

    with db_session.SessionLocal() as session:
        entity_repo = EntityRepository(session)
        result_repo = ResultRepository(session)
        matches = entity_repo.list_matches()
        pairs: list[tuple[Any, Any]] = []
        for match in matches:
            results = result_repo.list_results_for_match(match.id)
            if not results:
                continue
            pairs.append((match, results[0]))
    pairs.sort(
        key=lambda pair: pair[1].played_at
        if pair[1].played_at.tzinfo is not None
        else pair[1].played_at.replace(tzinfo=timezone.utc)
    )
    if not pairs:
        _print_json({"error": "no resulted matches found", "matches": 0})
        return
    cutoff_index = int(len(pairs) * args.train_fraction)
    train_pairs = pairs[:cutoff_index]
    test_pairs = pairs[cutoff_index:]
    if not train_pairs or not test_pairs:
        _print_json(
            {
                "error": "split produced an empty fold",
                "train": len(train_pairs),
                "test": len(test_pairs),
            }
        )
        return
    artifact = egs.train(train_pairs)
    if artifact is None:
        _print_json({"error": "train fold below MIN_TRAINING_SAMPLES", "train": len(train_pairs)})
        return
    booster_json = egs.load_booster_from_descriptor(artifact["booster_descriptor"])
    if booster_json is None:
        _print_json({"error": "could not load persisted booster"})
        return
    overall_xg = egs.evaluate_rmse(test_pairs, booster_json=booster_json)
    overall_baseline = egs.baseline_rmse(test_pairs)
    # Per-competition breakdown — sort by absolute RMSE delta so the
    # biggest wins/losses surface at the top of the report.
    by_competition: dict[str, list[tuple[Any, Any]]] = {}
    for pair in test_pairs:
        key = getattr(pair[0].competition, "name", None) or "_unknown"
        by_competition.setdefault(key, []).append(pair)
    rows: list[dict[str, Any]] = []
    for key, comp_pairs in by_competition.items():
        if len(comp_pairs) < 10:
            continue
        xg_metrics = egs.evaluate_rmse(comp_pairs, booster_json=booster_json)
        baseline_metrics = egs.baseline_rmse(comp_pairs)
        rows.append(
            {
                "competition": key,
                "test_samples": int(xg_metrics["samples"]),
                "xg_rmse": round(xg_metrics["rmse"], 4),
                "baseline_rmse": round(baseline_metrics["rmse"], 4),
                "delta_rmse": round(xg_metrics["rmse"] - baseline_metrics["rmse"], 4),
                "beats_baseline": xg_metrics["rmse"] < baseline_metrics["rmse"],
            }
        )
    rows.sort(key=lambda row: row["delta_rmse"])
    _print_json(
        {
            "train_samples": len(train_pairs),
            "test_samples": len(test_pairs),
            "train_fraction": args.train_fraction,
            "overall": {
                "xg_rmse": round(overall_xg["rmse"], 4),
                "baseline_rmse": round(overall_baseline["rmse"], 4),
                "delta_rmse": round(overall_xg["rmse"] - overall_baseline["rmse"], 4),
                "xg_mae": round(overall_xg["mae"], 4),
                "baseline_mae": round(overall_baseline["mae"], 4),
            },
            "by_competition": rows,
            "artifact": {
                "model_name": artifact["model_name"],
                "training_sample_size": artifact["training_sample_size"],
                "trained_at": artifact["trained_at"],
            },
        }
    )


def production_check(_: argparse.Namespace) -> None:
    errors = settings.production_config_errors() + migration_audit_errors()
    _print_json(
        {
            "environment": settings.environment,
            "database_url": settings.safe_database_url,
            "ready": not errors,
            "errors": errors,
        }
    )


def prune_source_documents(args: argparse.Namespace) -> None:
    """Delete source_documents that never got linked to a match or
    evidence row and that are older than the cutoff. These are the
    crawl crumbs that the ingestion pipeline emits while it searches
    for fixture identities — once stale, they are pure noise that
    pads the source_documents table (which today is the largest one
    in the schema by row count).

    Runs inside a single transaction; emits the deleted count so
    operators can verify expected fan-out before scheduling regular
    runs via cron."""
    from datetime import timedelta
    from sqlalchemy import and_, delete

    from app.models.tables import SourceDocumentModel

    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
        statement = delete(SourceDocumentModel).where(
            and_(
                SourceDocumentModel.matched_match_id.is_(None),
                SourceDocumentModel.linked_evidence_id.is_(None),
                SourceDocumentModel.captured_at < cutoff,
            )
        )
        if args.dry_run:
            from sqlalchemy import func, select

            count_stmt = (
                select(func.count())
                .select_from(SourceDocumentModel)
                .where(
                    and_(
                        SourceDocumentModel.matched_match_id.is_(None),
                        SourceDocumentModel.linked_evidence_id.is_(None),
                        SourceDocumentModel.captured_at < cutoff,
                    )
                )
            )
            count = session.scalar(count_stmt) or 0
            _print_json({"dry_run": True, "cutoff": cutoff.isoformat(), "would_delete": int(count)})
            return
        result = session.execute(statement)
        session.commit()
        _print_json(
            {
                "dry_run": False,
                "cutoff": cutoff.isoformat(),
                "deleted": int(getattr(result, "rowcount", None) or 0),
            }
        )
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proai", description="proAI operational commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser("refresh-current", help="Refresh the active Progol slate")
    refresh_parser.add_argument("--local-path", default=None)
    refresh_parser.set_defaults(func=refresh_current)

    job_parser = subparsers.add_parser("ensure-current-job", help="Create or update the current Progol refresh job")
    job_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=settings.current_progol_refresh_interval_minutes,
    )
    job_parser.add_argument("--job-name", default=settings.current_progol_refresh_job_name)
    job_parser.set_defaults(func=ensure_current_job)

    evaluate_parser = subparsers.add_parser("evaluate", help="Run historical evaluation reports")
    evaluate_parser.add_argument(
        "--mode",
        choices=["overall", "competitions", "calibration"],
        default="overall",
    )
    evaluate_parser.add_argument("--min-training-matches", type=int, default=12)
    evaluate_parser.add_argument("--confidence-threshold", type=float, default=0.5)
    evaluate_parser.set_defaults(func=evaluate)

    backtest_parser = subparsers.add_parser(
        "publish-backtest",
        help="Write per-competition walk-forward history to reports/backtest_history/",
    )
    backtest_parser.add_argument(
        "--output-dir",
        # Live under the persistent /data volume so the verdict
        # survives container rebuilds — otherwise the XGBoost gate
        # loses its source of truth every time we ship a code change.
        default="/data/backtest_history",
    )
    backtest_parser.add_argument("--min-training-matches", type=int, default=12)
    backtest_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress per-competition progress on stderr; JSON output is still written to stdout.",
    )
    backtest_parser.set_defaults(func=publish_backtest)

    check_parser = subparsers.add_parser("production-check", help="Validate production-facing settings")
    check_parser.set_defaults(func=production_check)

    xg_parser = subparsers.add_parser(
        "evaluate-xg",
        help="Walk-forward eval of the Sprint 7.1 xG model vs the league-mean baseline.",
    )
    xg_parser.add_argument(
        "--train-fraction",
        type=float,
        default=0.7,
        help="Fraction of matches (chronological) used for training; the rest is test.",
    )
    xg_parser.set_defaults(func=evaluate_xg)

    prune_parser = subparsers.add_parser(
        "prune-source-documents",
        help="Delete orphan source_documents older than the cutoff (unmatched + unlinked).",
    )
    prune_parser.add_argument("--older-than-days", type=int, default=90)
    prune_parser.add_argument("--dry-run", action="store_true", help="Count without deleting.")
    prune_parser.set_defaults(func=prune_source_documents)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
