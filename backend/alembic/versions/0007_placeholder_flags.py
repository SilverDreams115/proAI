"""Mark fallback teams and competitions as placeholders.

Revision ID: 0007_placeholder_flags
Revises: 0006_progol_slate_proposals
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op

revision = "0007_placeholder_flags"
down_revision = "0006_progol_slate_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE teams "
        "ADD COLUMN IF NOT EXISTS is_placeholder BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE competitions "
        "ADD COLUMN IF NOT EXISTS is_placeholder BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "UPDATE competitions SET is_placeholder = TRUE "
        "WHERE name LIKE 'Progol Concurso %'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS is_placeholder")
    op.execute("ALTER TABLE competitions DROP COLUMN IF EXISTS is_placeholder")
