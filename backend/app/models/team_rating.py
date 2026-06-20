"""SQLAlchemy models for productive team-rating persistence (R2).

Two immutable tables back the rating lifecycle described in
``docs/team_rating_design.md``:

* ``team_rating_runs``      — one row per deterministic recompute.
* ``team_rating_snapshots`` — one row per (team, namespace) per run.

Reproducibility comes from ``input_checksum`` / ``output_checksum`` and the
frozen ``config_json`` (mirrors the composition-hash contract used for
slates). Reads use the latest ``active`` run.

SAFETY / "blindada" notes:
  * These models are intentionally NOT imported by the app startup graph
    (``app/models/__init__.py`` stays empty, nothing wires the repository
    into a service). Production's ``run_migrations`` never calls
    ``Base.metadata.create_all`` on an existing v18 database, so importing
    this module changes no production schema.
  * The tables are created ONLY via :func:`create_team_rating_tables`, which
    is called from tests and from the confirm-gated CLI ``--apply`` path —
    never at import time and never from ``run_migrations``.
  * JSON is stored as ``Text`` (json string), matching every other JSON
    column in this codebase, so the same DDL works on SQLite (tests) and
    PostgreSQL (production). The future migration draft mirrors this exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import CheckConstraint
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.db.base import Base
from app.models.tables import generate_id
from app.models.tables import utc_now

# Mirror of the values the check constraints enforce. Kept as module
# constants so the repository / CLI validate against one source of truth.
VALID_RUN_STATUSES = ("computed", "active", "superseded")
VALID_NAMESPACES = ("club", "national", "unknown")
VALID_CONFIDENCE_BUCKETS = ("no_rating", "weak", "medium", "strong")

TEAM_RATING_TABLE_NAMES = ("team_rating_runs", "team_rating_snapshots")


class TeamRatingRunModel(Base):
    __tablename__ = "team_rating_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('computed','active','superseded')",
            name="ck_team_rating_run_status",
        ),
        Index("ix_team_rating_runs_status_created_at", "status", "created_at"),
        Index("ix_team_rating_runs_algorithm_version", "algorithm_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Frozen, reproducible TeamRatingConfig serialized as JSON (Text).
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rated_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    output_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="computed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TeamRatingSnapshotModel(Base):
    __tablename__ = "team_rating_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "team_id", "namespace", name="uq_team_rating_snapshot_identity"
        ),
        CheckConstraint(
            "namespace IN ('club','national','unknown')",
            name="ck_team_rating_snapshot_namespace",
        ),
        CheckConstraint(
            "confidence_bucket IN ('no_rating','weak','medium','strong')",
            name="ck_team_rating_snapshot_confidence",
        ),
        Index("ix_team_rating_snapshots_run_id", "run_id"),
        Index("ix_team_rating_snapshots_team_namespace", "team_id", "namespace"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("team_rating_runs.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    namespace: Mapped[str] = mapped_column(String(16), nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    rating_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    matches_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_bucket: Mapped[str] = mapped_column(
        String(16), nullable=False, default="no_rating"
    )
    last_result_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    competitions_seen_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


def create_team_rating_tables(bind: Connection | Engine) -> None:
    """Create ONLY the team_rating_* tables on ``bind``.

    Used by tests and the confirm-gated CLI ``--apply`` path. Never invoked
    at app startup nor from ``run_migrations``, so the production schema is
    never modified implicitly by importing this module.
    """
    Base.metadata.create_all(
        bind=bind,
        tables=[
            cast(Table, TeamRatingRunModel.__table__),
            cast(Table, TeamRatingSnapshotModel.__table__),
        ],
    )


def team_rating_tables_exist(bind: Connection | Engine) -> bool:
    """True only when BOTH team_rating_* tables are present on ``bind``."""
    existing = set(inspect(bind).get_table_names())
    return all(name in existing for name in TEAM_RATING_TABLE_NAMES)
