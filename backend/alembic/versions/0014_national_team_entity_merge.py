"""Merge Spanish-named national team placeholders into canonical English entities.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-05

No schema changes (no new columns or tables). The migration re-points
match.home_team_id / match.away_team_id rows that reference Spanish
placeholder entities (Bosnia, Croacia, Túnez, …) to the canonical
English entities already in the DB from TSDB ingestion. Team aliases
from the placeholders are moved to the canonical entities.

See _migrate_to_v14 in app/db/migrations.py for full details.
"""

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — no schema DDL.
    # The runtime migration (_migrate_to_v14) handles this at startup.
    pass


def downgrade() -> None:
    # Intentionally a no-op: reverting entity merges would require
    # recreating the placeholder rows and re-linking match references —
    # a destructive and operationally unsupported operation.
    pass
