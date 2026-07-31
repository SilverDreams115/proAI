"""Correct five PGM-806 competitions against the official LN guide.

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-31

No schema changes. Data-only migration. See _migrate_to_v34 in
app/db/migrations.py.

When ProgolFixtureResolver finds no ingested fixture for a pair it infers the
competition from the teams' shared history. For PGM-806 that inference was
wrong on five of nine positions, and the official guiamedia.pdf for concurso
806 states each one outright:

    pos 2  Oaxaca vs Sinaloa             "Jornada 2 de la Liga de Expansión"
    pos 5  Vasco da Gama vs I. Medellín  "Playoffs de la Copa Sudamericana"
    pos 6  O'Higgins vs Boca Jrs         "Playoffs de la Copa Sudamericana"
    pos 8  Talleres vs Vélez             "Jornada 2 de la Liga Argentina"
    pos 9  Central Córdoba vs Tucumán    "Jornada 2 de la Liga Argentina"

Positions 5 and 8 were not merely unresolved but confidently wrong: a
Sudamericana playoff sat under "Brasileirao" and a league fixture under "Copa
Libertadores", because that is where each pair had most often met before.

composition_hash is deliberately untouched — it fingerprints the raw promotion
payload before entity resolution, not the DB's competition rows, so existing
predictions and ticket snapshots keep their linkage.

Positions 1, 3, 4 and 7 are left alone: 3, 4 and 7 already match the guide, and
1 is the MLS/Liga MX All-Star exhibition, which belongs to no league.
"""

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
