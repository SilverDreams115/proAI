"""Add predictions.sanity_audit_json — full guardrail audit trace.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-16

One additive, nullable JSON/TEXT column holding the complete sanity-layer
trace per prediction: raw / display / decision / optimizer probability
vectors, sanity flags, evidence_level, risk_level, final_status,
sanity_policy_version, model_artifact_id, fallback_used and
is_international_friendly.

The existing home/draw/away_probability columns are intentionally left
untouched: they remain the MODEL-adjusted backtesting source. This column
only ADDS the decision-time trace; it never overwrites raw. Pre-sanity
rows stay NULL (no invented decisions).
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("sanity_audit_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "sanity_audit_json")
