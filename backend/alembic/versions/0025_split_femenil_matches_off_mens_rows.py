"""Move Liga MX Femenil fixtures off the men's rows that absorbed them.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-27

0024 split the row that had absorbed men's clubs. This is the same collision
running the other way, and it is the direction that actually costs
predictions: with "femenil" stripped from team names, "Cruz Azul Femenil"
resolved to the men's Cruz Azul, so most of Liga MX Femenil was ingested
straight onto men's rows. Eighteen of them carry 289 women's fixtures
between them — Cruz Azul 25, Querétaro 25, Juárez 24, Puebla 22, Atlas 22 —
polluting the recent-form features of the league Progol is mostly made of.

Every contaminated pair is Liga MX Femenil; no men's row holds fixtures from
another women's competition. Seven target rows already exist, under the
inconsistent names the sources produced ("C.F. Monterrey Femenil", "Club
Universidad Nacional Femenil", "Deportivo Toluca F.C. Femenil"), so the
mapping is written out explicitly instead of derived from the men's name —
minting "Monterrey Femenil" beside "C.F. Monterrey Femenil" would duplicate
the club, which is exactly the damage 0021 had to repair. The other eleven
have no women's row at all, because the collision is what prevented one from
ever being created.

"Washington Spirit" is deliberately excluded. It holds one CONCACAF W
fixture and no men's ones: an NWSL club whose real name carries no gender
marker, never contaminated.

Dry-run against the live data before writing this: 68 fixtures move onto the
seven existing rows with zero fixture-identity collisions. Data migration
only, no schema DDL, no deletes. ``_migrate_to_v25`` is the apply path;
``SCHEMA_VERSION`` is bumped to 25.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v25) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: folding women's fixtures back onto men's rows
    # would restore a corruption. Mirrors 0022/0023/0024.
    pass
