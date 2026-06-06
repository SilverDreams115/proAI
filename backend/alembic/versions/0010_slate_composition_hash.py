"""Track slate fixture composition with a hash to detect silent rereplacement.

When upsert_slate() receives the same draw_code with different fixtures it
now computes a SHA-256 hash, bumps slate_version, and marks prior ticket
snapshots as is_valid=False so stale recommendations are never served.

Revision ID: 0010_slate_composition_hash
Revises: 0009_slate_match_knockout_flag
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op

revision = "0010_slate_composition_hash"
down_revision = "0009_slate_match_knockout_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE progol_slates "
        "ADD COLUMN IF NOT EXISTS composition_hash VARCHAR(64)"
    )
    op.execute(
        "ALTER TABLE progol_slates "
        "ADD COLUMN IF NOT EXISTS slate_version INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots "
        "ADD COLUMN IF NOT EXISTS composition_hash VARCHAR(64)"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots "
        "ADD COLUMN IF NOT EXISTS is_valid BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots "
        "ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots "
        "ADD COLUMN IF NOT EXISTS invalidation_reason VARCHAR(120)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE progol_slates DROP COLUMN IF EXISTS composition_hash")
    op.execute("ALTER TABLE progol_slates DROP COLUMN IF EXISTS slate_version")
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots DROP COLUMN IF EXISTS composition_hash"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots DROP COLUMN IF EXISTS is_valid"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots DROP COLUMN IF EXISTS invalidated_at"
    )
    op.execute(
        "ALTER TABLE ticket_recommendation_snapshots DROP COLUMN IF EXISTS invalidation_reason"
    )
