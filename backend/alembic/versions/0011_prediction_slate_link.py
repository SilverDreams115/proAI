"""Link prediction audit rows to the slate that triggered them.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.add_column(sa.Column("slate_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("composition_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("slate_version", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_predictions_slate_id",
            "progol_slates",
            ["slate_id"],
            ["id"],
        )
        batch_op.create_index("ix_predictions_slate_id", ["slate_id"])
        batch_op.create_index(
            "ix_predictions_slate_match_generated",
            ["slate_id", "match_id", "generated_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.drop_index("ix_predictions_slate_match_generated")
        batch_op.drop_index("ix_predictions_slate_id")
        batch_op.drop_constraint("fk_predictions_slate_id", type_="foreignkey")
        batch_op.drop_column("slate_version")
        batch_op.drop_column("composition_hash")
        batch_op.drop_column("slate_id")
