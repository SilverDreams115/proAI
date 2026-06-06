"""Create progol_jornada_scores table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "progol_jornada_scores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slate_id", sa.String(36), sa.ForeignKey("progol_slates.id"), nullable=False),
        sa.Column("draw_code", sa.String(64), nullable=False),
        sa.Column("week_type", sa.String(32), nullable=False),
        sa.Column("composition_hash", sa.String(64), nullable=True),
        sa.Column("slate_version", sa.Integer(), nullable=True),
        sa.Column("total_matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches_with_results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("simple_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("simple_hit_rate", sa.Float(), nullable=True),
        sa.Column("ticket_hits", sa.Integer(), nullable=True),
        sa.Column("ticket_hit_rate", sa.Float(), nullable=True),
        sa.Column("brier_score_avg", sa.Float(), nullable=True),
        sa.Column("high_confidence_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_confidence_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("medium_confidence_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("medium_confidence_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low_confidence_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low_confidence_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("slate_id", "composition_hash", name="uq_jornada_score_slate_version"),
    )
    op.create_index("ix_jornada_scores_slate_id", "progol_jornada_scores", ["slate_id"])
    op.create_index("ix_jornada_scores_draw_code", "progol_jornada_scores", ["draw_code"])
    op.create_index("ix_jornada_scores_computed_at", "progol_jornada_scores", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_jornada_scores_computed_at", table_name="progol_jornada_scores")
    op.drop_index("ix_jornada_scores_draw_code", table_name="progol_jornada_scores")
    op.drop_index("ix_jornada_scores_slate_id", table_name="progol_jornada_scores")
    op.drop_table("progol_jornada_scores")
