"""Split the club rows that the femenil normalization collision merged.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-27

0023 stopped the collision from happening again. This one repairs what it
already produced.

While "femenil"/"femenino" were team stopwords every "Barcelona" normalized
to `barcelona`, and ``find_team_by_alias`` prefers a ``normalized_alias``
hit over a ``TeamModel.name`` hit — so three different clubs accumulated on
the women's row. "Barcelona Femenino" held 168 matches:

    SP1                              114   FC Barcelona (men)
    UEFA Champions League             26   FC Barcelona (men)
    Copa Libertadores                 26   Barcelona SC (Ecuador)
    UEFA Champions League Femenina     2   actually hers

The partition is read off the opponents, not guessed: SP1 and the UCL are
played against Real Madrid, Sevilla, Bayern, Inter and PSG; the Libertadores
fixtures are against Boca, River, Corinthians and El Nacional, and Barcelona
SC is the only Barcelona in that competition. The two women's fixtures are
against Lyonnes Femenino and stay where they are.

"Tigres UANL Femenil" holds one men's Liga MX fixture (Tijuana vs Tigres,
2026-07-17) by the same mechanism. The men's row already exists.

The target rows for Barcelona do NOT exist — the collision is exactly why
they were never created — so unlike the merge revisions this one inserts
them. Data migration only, no schema DDL, no deletes: the source rows keep
whatever genuinely belongs to them. ``_migrate_to_v24`` is the apply path;
``SCHEMA_VERSION`` is bumped to 24.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v24) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: folding the fixtures back onto one row would
    # restore a corruption, not a state worth returning to. Mirrors 0022/0023.
    pass
