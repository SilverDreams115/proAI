"""Fold the PG-2344 guide placeholders into the clubs they name.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-27

The Progol guide names clubs in short form. When a short form resolves to
nothing, slate promotion mints a placeholder row, and PG-2344 produced six
of them beside clubs the database already knew well:

    San Luis        -> Atletico de San Luis      (137 matches)
    Salt Lake       -> Real Salt Lake             (86)
    Portland        -> Portland Timbers           (85)
    Boca Jrs        -> CA Boca Juniors            (33)
    Inter De Milán  -> FC Internazionale Milano   (25)
    Barracas        -> Barracas Central           (23)

Each placeholder held exactly one fixture — the slate's — so the position
was scored as if the club had no history at all. Positions 12 and 13 came
out BLOQUEADO with 86 and 85 matches sitting unused on the real rows.

Every pair is verified against its fixture's competition rather than by
name similarity, which the note above ``_CONTINENTAL_SPLIT_MERGES`` warns
must never drive a merge: "San Luis" appears in a Liga MX fixture against
Tijuana, "Barracas" in an Argentine one against Deportivo Riestra, "Salt
Lake" and "Portland" in MLS fixtures, "Inter De Milán" in a Champions
League tie against Manchester City. No competing club exists in the
database for any of the six.

"Aberdeen" and "Hearts" are excluded: both are placeholders with no real
row behind them, because no registered source covers Scottish football.
That is missing data, not a naming mismatch.

Data migration only, no schema DDL, no deletes. ``_migrate_to_v27`` is the
apply path; ``SCHEMA_VERSION`` is bumped to 27.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v27) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: merge provenance is not recorded. Mirrors 0014/0021.
    pass
