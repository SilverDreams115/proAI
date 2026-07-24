"""Drop team rating persistence — team_rating_runs / team_rating_snapshots.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-24

The team-rating subsystem (ratings, feature adapter, approval gate, shadow mode
and the controlled canary) was removed in full: no model, service, script or
setting reads these tables any more. Keeping them would leave two orphan tables
whose schema no code owns, so this revision drops them and the runtime schema
goes back to matching ``Base.metadata`` exactly.

Snapshots are dropped before runs because ``team_rating_snapshots.run_id``
carries the FK to ``team_rating_runs.id``.

The runtime migrator (``app/db/migrations.py``) is the real apply path —
``SCHEMA_VERSION`` is bumped to 20 and ``_migrate_to_v20`` drops the same two
tables idempotently (DROP TABLE IF EXISTS). This file mirrors that DDL for
review parity; ``migration_audit_errors`` requires
``max(versions/) == SCHEMA_VERSION``.

DESTRUCTIVE: applying this revision deletes every stored rating run and
snapshot. ``downgrade()`` restores the 0019 schema (tables, indexes and
constraints) but cannot restore the rows — take a dump first if the historical
ratings matter.
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_team_rating_snapshots_team_namespace", table_name="team_rating_snapshots"
    )
    op.drop_index("ix_team_rating_snapshots_run_id", table_name="team_rating_snapshots")
    op.drop_table("team_rating_snapshots")
    op.drop_index(
        "ix_team_rating_runs_algorithm_version", table_name="team_rating_runs"
    )
    op.drop_index(
        "ix_team_rating_runs_status_created_at", table_name="team_rating_runs"
    )
    op.drop_table("team_rating_runs")


def downgrade() -> None:
    """Recreate the 0019 schema (structure only — the rows are gone)."""
    op.create_table(
        "team_rating_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("algorithm_version", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rated_match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded_match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_checksum", sa.String(64), nullable=False),
        sa.Column("output_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="computed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('computed','active','superseded')",
            name="ck_team_rating_run_status",
        ),
    )
    op.create_index(
        "ix_team_rating_runs_status_created_at",
        "team_rating_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_team_rating_runs_algorithm_version",
        "team_rating_runs",
        ["algorithm_version"],
    )

    op.create_table(
        "team_rating_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("team_rating_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.String(36),
            sa.ForeignKey("teams.id"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("rating_delta", sa.Float(), nullable=False, server_default="0"),
        sa.Column("matches_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goals_for", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goals_against", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_bucket", sa.String(16), nullable=False, server_default="no_rating"),
        sa.Column("last_result_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitions_seen_json", sa.Text(), nullable=False, server_default="[]"),
        sa.UniqueConstraint(
            "run_id", "team_id", "namespace", name="uq_team_rating_snapshot_identity"
        ),
        sa.CheckConstraint(
            "namespace IN ('club','national','unknown')",
            name="ck_team_rating_snapshot_namespace",
        ),
        sa.CheckConstraint(
            "confidence_bucket IN ('no_rating','weak','medium','strong')",
            name="ck_team_rating_snapshot_confidence",
        ),
    )
    op.create_index(
        "ix_team_rating_snapshots_run_id", "team_rating_snapshots", ["run_id"]
    )
    op.create_index(
        "ix_team_rating_snapshots_team_namespace",
        "team_rating_snapshots",
        ["team_id", "namespace"],
    )
