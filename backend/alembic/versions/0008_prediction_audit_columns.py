"""Add audit fields to the predictions table.

Revision ID: 0008_prediction_audit_columns
Revises: 0007_placeholder_flags
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op

revision = "0008_prediction_audit_columns"
down_revision = "0007_placeholder_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE predictions "
        "ADD COLUMN IF NOT EXISTS competition_readiness VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE predictions "
        "ADD COLUMN IF NOT EXISTS blocked_reason VARCHAR(120)"
    )
    op.execute(
        "ALTER TABLE predictions "
        "ADD COLUMN IF NOT EXISTS anchors_json TEXT NOT NULL DEFAULT '{}'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_predictions_match_generated "
        "ON predictions (match_id, generated_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_predictions_match_generated")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS anchors_json")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS blocked_reason")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS competition_readiness")
