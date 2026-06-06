"""Staging table for upcoming Progol contests pulled from the LN PDF.

Revision ID: 0006_progol_slate_proposals
Revises: 0005_ticket_recommendation_snapshots
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op

revision = "0006_progol_slate_proposals"
down_revision = "0005_ticket_recommendation_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS progol_slate_proposals (
            id VARCHAR(36) PRIMARY KEY,
            draw_code VARCHAR(64) NOT NULL,
            week_type VARCHAR(32) NOT NULL DEFAULT 'weekend',
            source_name VARCHAR(120) NOT NULL,
            source_url VARCHAR(500) NOT NULL,
            registration_closes_at TIMESTAMP WITH TIME ZONE,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status VARCHAR(32) NOT NULL DEFAULT 'observed',
            observations INTEGER NOT NULL DEFAULT 1,
            first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
            promoted_slate_id VARCHAR(36),
            FOREIGN KEY(promoted_slate_id) REFERENCES progol_slates (id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_progol_proposal_source
        ON progol_slate_proposals (draw_code, source_url)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_progol_slate_proposals_draw_code
        ON progol_slate_proposals (draw_code)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_progol_slate_proposals_status
        ON progol_slate_proposals (status)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS progol_slate_proposals")
