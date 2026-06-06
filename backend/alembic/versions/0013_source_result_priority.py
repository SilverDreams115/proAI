"""Add result_source_priority to sources.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column(
                "result_source_priority",
                sa.Integer(),
                nullable=False,
                server_default="50",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("result_source_priority")
