"""Persistence for productive team ratings (R2).

Thin repository over ``team_rating_runs`` / ``team_rating_snapshots``.
Follows the project convention: takes a ``Session``, ``flush()``es but does
NOT commit (the caller owns the transaction, e.g. ``managed_transaction``).

Deliberately isolated:
  * no FeatureService / PredictionService / worker imports;
  * no schema creation (callers ensure the tables exist);
  * reads default to the latest ``active`` run so a future feature layer can
    answer "which rating was active" without guessing.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.team_rating import VALID_RUN_STATUSES
from app.models.team_rating import TeamRatingRunModel
from app.models.team_rating import TeamRatingSnapshotModel


class TeamRatingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- runs ---------------------------------------------------------------

    def create_run(
        self,
        *,
        algorithm_version: str,
        config_json: str,
        source_result_count: int,
        rated_match_count: int,
        excluded_match_count: int,
        input_checksum: str,
        output_checksum: str,
        status: str = "computed",
    ) -> TeamRatingRunModel:
        if status not in VALID_RUN_STATUSES:
            raise ValueError(f"invalid run status {status!r}")
        run = TeamRatingRunModel(
            algorithm_version=algorithm_version,
            config_json=config_json,
            source_result_count=source_result_count,
            rated_match_count=rated_match_count,
            excluded_match_count=excluded_match_count,
            input_checksum=input_checksum,
            output_checksum=output_checksum,
            status=status,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_run(self, run_id: str) -> TeamRatingRunModel | None:
        return self.session.get(TeamRatingRunModel, run_id)

    def get_latest_active_run(
        self, algorithm_version: str
    ) -> TeamRatingRunModel | None:
        stmt = (
            select(TeamRatingRunModel)
            .where(
                TeamRatingRunModel.algorithm_version == algorithm_version,
                TeamRatingRunModel.status == "active",
            )
            .order_by(TeamRatingRunModel.created_at.desc())
        )
        return self.session.scalars(stmt).first()

    def active_run_with_checksum(
        self, algorithm_version: str, input_checksum: str
    ) -> TeamRatingRunModel | None:
        """An already-active run for this version + identical input.

        Lets the CLI ``--apply`` abort when the same input is already live
        (no point re-persisting identical ratings)."""
        stmt = select(TeamRatingRunModel).where(
            TeamRatingRunModel.algorithm_version == algorithm_version,
            TeamRatingRunModel.status == "active",
            TeamRatingRunModel.input_checksum == input_checksum,
        )
        return self.session.scalars(stmt).first()

    def supersede_previous_active(self, algorithm_version: str) -> int:
        """Mark every currently-active run for ``algorithm_version`` as
        ``superseded``. Returns how many rows changed. Caller commits."""
        runs = self.session.scalars(
            select(TeamRatingRunModel).where(
                TeamRatingRunModel.algorithm_version == algorithm_version,
                TeamRatingRunModel.status == "active",
            )
        ).all()
        for run in runs:
            run.status = "superseded"
        self.session.flush()
        return len(runs)

    def mark_run_active(self, run_id: str) -> TeamRatingRunModel:
        run = self.session.get(TeamRatingRunModel, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        run.status = "active"
        self.session.flush()
        return run

    # -- snapshots ----------------------------------------------------------

    def bulk_insert_snapshots(
        self, run_id: str, snapshots: Iterable[dict]
    ) -> int:
        """Insert snapshot rows for ``run_id``. Each dict carries the
        snapshot columns (without ``run_id``/``id``). Returns the count."""
        rows = [TeamRatingSnapshotModel(run_id=run_id, **snap) for snap in snapshots]
        if rows:
            self.session.add_all(rows)
            self.session.flush()
        return len(rows)

    def get_snapshots_for_run(self, run_id: str) -> Sequence[TeamRatingSnapshotModel]:
        stmt = select(TeamRatingSnapshotModel).where(
            TeamRatingSnapshotModel.run_id == run_id
        )
        return list(self.session.scalars(stmt))

    def get_team_snapshot(
        self,
        team_id: str,
        namespace: str,
        *,
        run_id: str | None = None,
        algorithm_version: str | None = None,
    ) -> TeamRatingSnapshotModel | None:
        """One team's snapshot for an explicit ``run_id`` or, when omitted,
        the latest ``active`` run of ``algorithm_version``."""
        if run_id is None:
            if algorithm_version is None:
                raise ValueError("run_id or algorithm_version is required")
            active = self.get_latest_active_run(algorithm_version)
            if active is None:
                return None
            run_id = active.id
        stmt = select(TeamRatingSnapshotModel).where(
            TeamRatingSnapshotModel.run_id == run_id,
            TeamRatingSnapshotModel.team_id == team_id,
            TeamRatingSnapshotModel.namespace == namespace,
        )
        return self.session.scalars(stmt).first()
