"""Adaptive retraining gate.

Evaluates whether enough canonical, scored Progol jornada data exists to
justify re-running the XGBoost training pipeline, and executes the retrain
under strict safety gates:

  - Never removes a previous ModelTrainingRunModel (rollback is always
    possible by reverting the latest_run pointer).
  - Never modifies PG-2336 or any live slate.
  - Never uses results from conflicting sources.
  - Never uses predictions without a slate_id link.
  - Never triggers automatically — every retrain is operator-initiated.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchModel, ModelTrainingRunModel, ProgolJornadaScoreModel
from app.repositories.jornada_score_repository import JornadaScoreRepository
from app.repositories.training_repository import TrainingRepository
from app.schemas.adaptive_dataset import AdaptiveDatasetRow
from app.schemas.adaptive_retraining import (
    BandComparison,
    DryRunReport,
    ModelComparison,
    ReadinessCheck,
    ReadinessReport,
    RetrainingResult,
    RetrainingThresholds,
    WeekTypeComparison,
)
from app.services.adaptive_dataset_service import AdaptiveDatasetService

logger = logging.getLogger(__name__)

_RECOMMENDED_ACTIONS = (
    "skip",
    "recalibrate_only",
    "confidence_band_adjustment",
    "full_xgboost_retrain",
)


class AdaptiveRetrainingService:
    MODEL_NAME = "elo_poisson_blend"

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_readiness(
        self,
        thresholds: RetrainingThresholds | None = None,
    ) -> ReadinessReport:
        """Inspect the current adaptive dataset and return a readiness verdict.

        Does NOT modify any DB state.
        """
        thresholds = thresholds or RetrainingThresholds()
        summary = AdaptiveDatasetService(self.session).build_summary()
        last_run = TrainingRepository(self.session).latest_run(self.MODEL_NAME)
        new_rows = self._count_new_rows_since_run(last_run)

        total_rows = summary.total_rows
        complete_slates = summary.total_slates_complete

        blocked_band = summary.by_confidence_band.get("blocked")
        blocked_total = blocked_band.total if blocked_band else 0
        blocked_rate = round(blocked_total / total_rows, 4) if total_rows > 0 else 0.0

        results_seen = summary.rows_with_canonical_result + summary.rows_with_conflict
        conflict_rate = (
            round(summary.rows_with_conflict / results_seen, 4) if results_seen > 0 else 0.0
        )

        checks = self._run_checks(
            total_rows=total_rows,
            complete_slates=complete_slates,
            conflict_rate=conflict_rate,
            blocked_rate=blocked_rate,
            new_rows=new_rows,
            thresholds=thresholds,
        )
        ready = all(c.passed for c in checks)
        action = self._recommended_action(
            total_rows=total_rows,
            complete_slates=complete_slates,
            conflict_rate=conflict_rate,
            blocked_rate=blocked_rate,
            new_rows=new_rows,
            thresholds=thresholds,
        )

        return ReadinessReport(
            ready=ready,
            recommended_action=action,
            trainable_rows=total_rows,
            complete_slates=complete_slates,
            blocked_rate=blocked_rate,
            conflict_rate=conflict_rate,
            new_rows_since_last_train=new_rows,
            last_training_run_id=last_run.id if last_run else None,
            last_training_run_at=last_run.trained_at if last_run else None,
            checks=checks,
            thresholds=thresholds,
        )

    def dry_run(
        self,
        thresholds: RetrainingThresholds | None = None,
    ) -> DryRunReport:
        """Return a readiness verdict without modifying any DB state."""
        report = self.evaluate_readiness(thresholds)
        return DryRunReport(**report.model_dump())

    def run_retraining_if_ready(
        self,
        thresholds: RetrainingThresholds | None = None,
    ) -> RetrainingResult:
        """Execute the retrain pipeline if readiness gates pass.

        Safety contract:
        - Previous ModelTrainingRunModel rows are never deleted.
        - Returns success=False (not an exception) when not ready so API
          callers can inspect reasons without try/except.
        """
        thresholds = thresholds or RetrainingThresholds()
        report = self.evaluate_readiness(thresholds)

        if not report.ready:
            return RetrainingResult(
                success=False,
                ready=False,
                recommended_action=report.recommended_action,
                reasons=[c.reason for c in report.checks if not c.passed],
                training_run_id=None,
                rollback_run_id=report.last_training_run_id,
                comparison=None,
            )

        rollback_run_id = report.last_training_run_id
        rows = self._build_all_rows()

        logger.info(
            "adaptive_retrain_starting",
            extra={
                "event": "adaptive_retrain_starting",
                "recommended_action": report.recommended_action,
                "trainable_rows": report.trainable_rows,
                "complete_slates": report.complete_slates,
                "rollback_run_id": rollback_run_id,
            },
        )

        # Run the training pipeline — always saves a new ModelTrainingRunModel.
        mts = self._make_training_service()
        mts.train()

        new_run = TrainingRepository(self.session).latest_run(self.MODEL_NAME)
        new_artifact = json.loads(new_run.artifact_json) if new_run else {}

        comparison = self.compare_against_current_model(new_artifact, rows) if rows else None

        logger.info(
            "adaptive_retrain_complete",
            extra={
                "event": "adaptive_retrain_complete",
                "training_run_id": new_run.id if new_run else None,
                "brier_before": comparison.brier_score_before if comparison else None,
                "brier_after": comparison.brier_score_after if comparison else None,
                "improved": comparison.improved if comparison else None,
            },
        )

        return RetrainingResult(
            success=True,
            ready=True,
            recommended_action=report.recommended_action,
            reasons=[],
            training_run_id=new_run.id if new_run else None,
            rollback_run_id=rollback_run_id,
            comparison=comparison,
        )

    def build_training_window(self) -> dict[str, Any]:
        """Return a summary of the current training window.

        Includes all trainable rows from complete jornadas, the count of
        new rows since the last training run, and the last run timestamp.
        Only useful for operator inspection; does not trigger training.
        """
        summary = AdaptiveDatasetService(self.session).build_summary()
        last_run = TrainingRepository(self.session).latest_run(self.MODEL_NAME)
        new_rows = self._count_new_rows_since_run(last_run)
        return {
            "complete_slates": summary.total_slates_complete,
            "total_slates_scored": summary.total_slates_scored,
            "trainable_rows": summary.total_rows,
            "rows_with_canonical_result": summary.rows_with_canonical_result,
            "rows_with_conflict": summary.rows_with_conflict,
            "new_rows_since_last_train": new_rows,
            "last_training_run_id": last_run.id if last_run else None,
            "last_training_run_at": last_run.trained_at.isoformat() if last_run else None,
            "by_confidence_band": {
                band: {"total": v.total, "hits": v.hits, "hit_rate": v.hit_rate}
                for band, v in summary.by_confidence_band.items()
            },
        }

    def compare_against_current_model(
        self,
        new_artifact: dict[str, Any],
        rows: list[AdaptiveDatasetRow],
    ) -> ModelComparison:
        """Compute before/after prediction quality on the adaptive dataset.

        "Before" = brier_score / hit stored in the adaptive rows (computed
        at the time predictions were originally made with the old model).
        "After"  = re-scoring the same matches through the new artifact.
        """
        if not rows:
            return ModelComparison(
                rows_evaluated=0,
                brier_score_before=None,
                brier_score_after=None,
                brier_delta=None,
                hit_rate_before=None,
                hit_rate_after=None,
                hit_rate_delta=None,
                improved=False,
                by_confidence_band=[],
                by_week_type=[],
            )

        # ---- Before metrics (already stored in adaptive rows) ----------
        b_briers = [r.brier_score for r in rows if r.brier_score is not None]
        b_hits = [int(r.hit) for r in rows if r.hit is not None]
        brier_before = round(mean(b_briers), 4) if b_briers else None
        hit_before = round(mean(b_hits), 4) if b_hits else None

        # ---- After metrics (re-score with new artifact) ----------------
        match_ids = [r.match_id for r in rows]
        matches_by_id = self._load_matches(match_ids)
        mts = self._make_training_service()

        after: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.actual_result not in ("1", "X", "2"):
                continue
            match = matches_by_id.get(row.match_id)
            if match is None:
                continue
            try:
                scored = mts._score_match_with_artifact(match, new_artifact)
            except Exception:
                continue
            pred_key = max(scored, key=scored.get)
            pred_label = {"home": "1", "draw": "X", "away": "2"}[pred_key]
            actual_idx = {"1": 0, "X": 1, "2": 2}[row.actual_result]
            probs = [scored["home"], scored["draw"], scored["away"]]
            brier = mts._brier_score(probs, actual_idx)
            after[row.match_id] = {
                "hit": pred_label == row.actual_result,
                "brier": brier,
            }

        a_briers = [v["brier"] for v in after.values()]
        a_hits = [int(v["hit"]) for v in after.values()]
        brier_after = round(mean(a_briers), 4) if a_briers else None
        hit_after = round(mean(a_hits), 4) if a_hits else None

        brier_delta = (
            round(brier_before - brier_after, 4)
            if brier_before is not None and brier_after is not None
            else None
        )
        hit_delta = (
            round(hit_after - hit_before, 4)
            if hit_before is not None and hit_after is not None
            else None
        )
        improved = bool(brier_delta is not None and brier_delta > 0)

        # ---- Band breakdown --------------------------------------------
        by_band_b: dict[str, dict[str, list]] = defaultdict(lambda: {"hits": [], "briers": []})
        by_band_a: dict[str, dict[str, list]] = defaultdict(lambda: {"hits": [], "briers": []})
        by_week_b: dict[str, dict[str, list]] = defaultdict(lambda: {"hits": [], "briers": []})
        by_week_a: dict[str, dict[str, list]] = defaultdict(lambda: {"hits": [], "briers": []})

        for row in rows:
            band = row.confidence_band or "low"
            wt = row.week_type
            if row.hit is not None:
                by_band_b[band]["hits"].append(int(row.hit))
            if row.brier_score is not None:
                by_band_b[band]["briers"].append(row.brier_score)
            if row.hit is not None:
                by_week_b[wt]["hits"].append(int(row.hit))
            if row.brier_score is not None:
                by_week_b[wt]["briers"].append(row.brier_score)
            if row.match_id in after:
                m = after[row.match_id]
                by_band_a[band]["hits"].append(int(m["hit"]))
                by_band_a[band]["briers"].append(m["brier"])
                by_week_a[wt]["hits"].append(int(m["hit"]))
                by_week_a[wt]["briers"].append(m["brier"])

        band_comparisons = [
            BandComparison(
                band=band,
                hits_before=sum(by_band_b[band]["hits"]),
                total_before=len(by_band_b[band]["hits"]),
                hit_rate_before=_safe_mean(by_band_b[band]["hits"]),
                hits_after=sum(by_band_a[band]["hits"]),
                total_after=len(by_band_a[band]["hits"]),
                hit_rate_after=_safe_mean(by_band_a[band]["hits"]),
                brier_before=_safe_mean(by_band_b[band]["briers"]),
                brier_after=_safe_mean(by_band_a[band]["briers"]),
            )
            for band in sorted(by_band_b.keys() | by_band_a.keys())
        ]

        week_comparisons = [
            WeekTypeComparison(
                week_type=wt,
                hits_before=sum(by_week_b[wt]["hits"]),
                total_before=len(by_week_b[wt]["hits"]),
                hit_rate_before=_safe_mean(by_week_b[wt]["hits"]),
                hits_after=sum(by_week_a[wt]["hits"]),
                total_after=len(by_week_a[wt]["hits"]),
                hit_rate_after=_safe_mean(by_week_a[wt]["hits"]),
                brier_before=_safe_mean(by_week_b[wt]["briers"]),
                brier_after=_safe_mean(by_week_a[wt]["briers"]),
            )
            for wt in sorted(by_week_b.keys() | by_week_a.keys())
        ]

        return ModelComparison(
            rows_evaluated=len(rows),
            brier_score_before=brier_before,
            brier_score_after=brier_after,
            brier_delta=brier_delta,
            hit_rate_before=hit_before,
            hit_rate_after=hit_after,
            hit_rate_delta=hit_delta,
            improved=improved,
            by_confidence_band=band_comparisons,
            by_week_type=week_comparisons,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_checks(
        self,
        *,
        total_rows: int,
        complete_slates: int,
        conflict_rate: float,
        blocked_rate: float,
        new_rows: int,
        thresholds: RetrainingThresholds,
    ) -> list[ReadinessCheck]:
        return [
            ReadinessCheck(
                name="min_trainable_rows",
                passed=total_rows >= thresholds.min_trainable_rows,
                value=total_rows,
                threshold=thresholds.min_trainable_rows,
                reason=(
                    f"Need ≥{thresholds.min_trainable_rows} trainable rows; have {total_rows}."
                    if total_rows < thresholds.min_trainable_rows
                    else f"OK: {total_rows} trainable rows."
                ),
            ),
            ReadinessCheck(
                name="min_complete_slates",
                passed=complete_slates >= thresholds.min_complete_slates,
                value=complete_slates,
                threshold=thresholds.min_complete_slates,
                reason=(
                    f"Need ≥{thresholds.min_complete_slates} complete slates; have {complete_slates}."
                    if complete_slates < thresholds.min_complete_slates
                    else f"OK: {complete_slates} complete slates."
                ),
            ),
            ReadinessCheck(
                name="max_conflict_rate",
                passed=conflict_rate <= thresholds.max_conflict_rate,
                value=conflict_rate,
                threshold=thresholds.max_conflict_rate,
                reason=(
                    f"Conflict rate {conflict_rate:.1%} exceeds max {thresholds.max_conflict_rate:.1%}; "
                    "resolve source conflicts before retraining."
                    if conflict_rate > thresholds.max_conflict_rate
                    else f"OK: conflict rate {conflict_rate:.1%}."
                ),
            ),
        ]

    @staticmethod
    def _recommended_action(
        *,
        total_rows: int,
        complete_slates: int,
        conflict_rate: float,
        blocked_rate: float,
        new_rows: int,
        thresholds: RetrainingThresholds,
    ) -> str:
        if (
            total_rows < thresholds.min_trainable_rows
            or complete_slates < thresholds.min_complete_slates
            or conflict_rate > thresholds.max_conflict_rate
        ):
            return "skip"
        if blocked_rate > thresholds.max_blocked_rate_for_full_retrain:
            return "confidence_band_adjustment"
        if new_rows < thresholds.min_new_rows_since_last_train:
            return "recalibrate_only"
        return "full_xgboost_retrain"

    def _count_new_rows_since_run(self, last_run: ModelTrainingRunModel | None) -> int:
        """Count trainable rows from complete jornadas scored after last_run."""
        stmt = select(ProgolJornadaScoreModel).where(
            ProgolJornadaScoreModel.is_complete.is_(True),
            ProgolJornadaScoreModel.composition_hash.is_not(None),
        )
        if last_run is not None:
            stmt = stmt.where(ProgolJornadaScoreModel.computed_at > last_run.trained_at)
        new_scores = list(self.session.scalars(stmt))
        total = 0
        for score in new_scores:
            rows = AdaptiveDatasetService(self.session).build_rows_for_slate(score.slate_id)
            total += len(rows)
        return total

    def _build_all_rows(self) -> list[AdaptiveDatasetRow]:
        """Return trainable rows from all complete jornadas."""
        svc = AdaptiveDatasetService(self.session)
        scores = JornadaScoreRepository(self.session).list_history(limit=200)
        rows: list[AdaptiveDatasetRow] = []
        for score in scores:
            if score.is_complete and score.composition_hash:
                rows.extend(svc.build_rows_for_slate(score.slate_id))
        return rows

    def _load_matches(self, match_ids: list[str]) -> dict[str, MatchModel]:
        if not match_ids:
            return {}
        stmt = select(MatchModel).where(MatchModel.id.in_(match_ids))
        return {m.id: m for m in self.session.scalars(stmt)}

    def _make_training_service(self):
        from app.repositories.entity_repository import EntityRepository
        from app.repositories.result_repository import ResultRepository
        from app.services.model_training_service import ModelTrainingService

        return ModelTrainingService(
            training_repository=TrainingRepository(self.session),
            entity_repository=EntityRepository(self.session),
            result_repository=ResultRepository(self.session),
        )


def _safe_mean(values: list) -> float | None:
    return round(mean(values), 4) if values else None
