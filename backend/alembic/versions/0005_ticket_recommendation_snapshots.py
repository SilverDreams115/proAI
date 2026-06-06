"""Add auditable ticket recommendation snapshots.

Revision ID: 0005_ticket_recommendation_snapshots
Revises:
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op

revision = "0005_ticket_recommendation_snapshots"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_recommendation_snapshots (
            id VARCHAR(36) PRIMARY KEY,
            slate_id VARCHAR(36) NOT NULL,
            generated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            model_version VARCHAR(120) NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(slate_id) REFERENCES progol_slates (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ticket_recommendation_snapshots_slate_id
        ON ticket_recommendation_snapshots (slate_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ticket_recommendation_snapshots_generated_at
        ON ticket_recommendation_snapshots (generated_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ticket_recommendation_snapshots")
