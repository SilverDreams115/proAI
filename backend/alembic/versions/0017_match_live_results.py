"""Create match_live_results table for live/partial/final observations.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-15

Kept separate from match_results so the canonical-final store and
CanonicalResultRepository are never polluted by in-progress scores.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_live_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("match_id", sa.String(36), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("home_goals", sa.Integer(), nullable=True),
        sa.Column("away_goals", sa.Integer(), nullable=True),
        sa.Column("result_code", sa.String(1), nullable=True),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("match_id", "source_id", name="uq_match_live_identity"),
    )
    op.create_index("ix_match_live_results_match_id", "match_live_results", ["match_id"])
    op.create_index("ix_match_live_results_source_id", "match_live_results", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_match_live_results_source_id", table_name="match_live_results")
    op.drop_index("ix_match_live_results_match_id", table_name="match_live_results")
    op.drop_table("match_live_results")
