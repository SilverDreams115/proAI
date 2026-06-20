"""DRAFT migration — team rating persistence (R2). NOT APPLIED.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-20

⚠️  THIS FILE IS A DRAFT AND LIVES OUTSIDE ``alembic/versions/`` ON PURPOSE.

It is therefore:
  * NOT autodiscovered by ``alembic upgrade`` (alembic only scans
    ``versions/``), so it can never be applied accidentally;
  * NOT scanned by ``app.db.migrations.migration_audit_errors`` (which globs
    ``versions/*.py``), so the runtime ``SCHEMA_VERSION`` (18) still matches
    the latest *applied* alembic revision and the app keeps booting and the
    test suite keeps passing.

ACTIVATION PROTOCOL (future, manual, confirm-gated — see
``docs/team_rating_activation_protocol.md``):
  1. Move this file into ``backend/alembic/versions/`` as
     ``0019_team_rating_persistence.py``.
  2. Bump ``SCHEMA_VERSION`` to 19 in ``app/db/migrations.py`` and add a
     ``_migrate_to_v19`` that creates the same two tables (CREATE TABLE IF
     NOT EXISTS, like ``_migrate_to_v17``), wired into both
     ``_run_migrations_unlocked`` and ``_bootstrap_schema``.
  3. Only then does the schema reach a real database.

DDL parity: JSON is stored as ``Text`` (json string), matching every other
JSON column in this codebase (``payload_json``, ``anchors_json``,
``sanity_audit_json`` …), so the identical DDL runs on SQLite (tests) and
PostgreSQL (production). The SQLAlchemy models in
``app/models/team_rating.py`` mirror this file column-for-column.
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
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
