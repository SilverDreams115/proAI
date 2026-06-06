"""Mark knockout positions so the boleta recommendation never picks "X".

Revision ID: 0009_slate_match_knockout_flag
Revises: 0008_prediction_audit_columns
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op

revision = "0009_slate_match_knockout_flag"
down_revision = "0008_prediction_audit_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE progol_slate_matches "
        "ADD COLUMN IF NOT EXISTS is_knockout BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE progol_slate_matches DROP COLUMN IF EXISTS is_knockout")
