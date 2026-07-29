"""Mark the fixtures the Progol promotion path fabricated.

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-29

When ``ProgolFixtureResolver`` finds no ingested match for a pair the LN guide
lists, promotion still has to produce 9 or 14 positions, so it builds one:
kickoff at ``cierre + 12h``, stepped an hour per position, with a competition
inferred from team history. Nothing recorded that the row was a construction,
so it sat in ``matches`` looking exactly like a fixture a feed had reported.

Three consequences, all live in production before this revision:

* the pick card printed the invented hour as the fixture's kickoff;
* ``find_upcoming_match_for_pair`` returned a previous slate's fabricated row as
  the "real match" for a new slate, so 16 match rows ended up shared between two
  slates (PG-2336/PGM-799, PG-2337/PGM-800, PG-2334/PGR-2334);
* correcting a cierre never moved the kickoffs derived from the old one — PGM-806
  closed 2026-07-28 22:55Z with all 9 of its kickoffs on 2026-07-30.

The backfill recognises fabricated rows by their shape rather than by recomputing
``cierre + 12h``: once an operator corrects the cierre the stored kickoffs no
longer relate to it, and those are exactly the rows that most need marking.
Within a slate of >= 3 positions, kickoffs spaced at exactly one hour that
collapse to a single base are the fabricator's signature — 10 of the 16 slates in
production match it, including the active PG-2344.

Additive and conservative: rows only gain the mark, the default is false, and a
slate that resolved to real fixtures forms no ladder and is left alone.
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE matches ADD COLUMN IF NOT EXISTS is_placeholder BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        """
        UPDATE matches SET is_placeholder = TRUE WHERE id IN (
            SELECT sm.match_id
            FROM progol_slate_matches sm
            WHERE sm.slate_id IN (
                SELECT inner_sm.slate_id
                FROM progol_slate_matches inner_sm
                JOIN matches m ON m.id = inner_sm.match_id
                GROUP BY inner_sm.slate_id
                HAVING COUNT(*) >= 3
                   AND COUNT(DISTINCT m.kickoff_at
                             - (inner_sm.position - 1) * INTERVAL '1 hour') = 1
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE matches DROP COLUMN IF EXISTS is_placeholder")
