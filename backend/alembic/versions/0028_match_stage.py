"""Persist the competition stage/round a fixture belongs to.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-28

TheSportsDB returns ``strRound`` on every event and the connector threw it
away, keeping only teams, kickoff and score. Nothing downstream could then
tell apart two fixtures that share a competition name but not a phase:

    UEFA Champions League  group match   vs  semi-final
    Liga MX                week 3        vs  liguilla final

Both distinctions matter to a Progol boleta. Knockout ties are graded on
the 90-minute result like any other position, but two sides that must
produce a winner draw less often, so the prediction service trims the draw
mass for them. Deciding which positions get that trim was left entirely to
an operator clicking the knockout endpoint, and the two real fixtures ever
flagged in production — "Paris SG vs Arsenal" filed under competition "E0",
and a "Toluca vs Tigres" liguilla final filed under plain "Liga MX" —
show why the competition name alone can never recover it.

The column is nullable and additive. Existing rows keep NULL, every
consumer reads NULL as "unknown stage" and falls back to the previous
name-based behaviour, so this revision changes no prediction on its own.
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS stage VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE matches DROP COLUMN IF EXISTS stage")
