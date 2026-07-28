"""Fold the seven remaining split club rows before they cost a slate.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-28

0030 repaired two splits *after* they had already blocked two PG-2344
positions. These seven are the same defect caught before it lands, found
by ``app.services.team_row_split_detection`` scanning every team row.

    Vancouver (2)    -> Vancouver Whitecaps (91)      MLS
    Minnesota (3)    -> Minnesota United (88)         MLS
    San Jose (2)     -> San Jose Earthquakes (82)     MLS
    New England (2)  -> New England Revolution (82)   MLS
    Malmo (10)       -> Malmo FF (72)                 Swedish Allsvenskan
    Shimizu (3)      -> Shimizu S-Pulse (58)          J1 League
    Kalmar (10)      -> Kalmar FF (39)                Swedish Allsvenskan

Each pair shares a competition, which is the check the note above
``_CONTINENTAL_SPLIT_MERGES`` requires and the only reason these are
safe: name similarity on its own proposed "Real Sociedad" -> "Real
Madrid" and "Braga" -> "Bragantino" against the same data.

None of these rows is on an open slate today. They are folded now
because the cost only appears at the worst moment — when a Progol guide
writes the short form and the position is scored on two matches of
history.
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_MERGES = (
    ("Vancouver", "Vancouver Whitecaps"),
    ("Minnesota", "Minnesota United"),
    ("San Jose", "San Jose Earthquakes"),
    ("New England", "New England Revolution"),
    ("Malmö", "Malmo FF"),
    ("Shimizu", "Shimizu S-Pulse"),
    ("Kalmar", "Kalmar FF"),
)


def upgrade() -> None:
    connection = op.get_bind()
    for placeholder_name, canonical_name in _MERGES:
        placeholder = connection.exec_driver_sql(
            "SELECT id FROM teams WHERE name = %(n)s LIMIT 1", {"n": placeholder_name}
        ).fetchone()
        canonical = connection.exec_driver_sql(
            "SELECT id FROM teams WHERE name = %(n)s LIMIT 1", {"n": canonical_name}
        ).fetchone()
        if placeholder is None or canonical is None or placeholder[0] == canonical[0]:
            continue
        params = {"p": placeholder[0], "c": canonical[0]}
        for side in ("home_team_id", "away_team_id"):
            connection.exec_driver_sql(
                f"""
                UPDATE matches SET {side} = %(c)s
                WHERE {side} = %(p)s AND NOT EXISTS (
                    SELECT 1 FROM matches m2
                    WHERE m2.id != matches.id
                      AND m2.competition_id = matches.competition_id
                      AND m2.{side} = %(c)s
                      AND m2.kickoff_at = matches.kickoff_at
                )
                """,
                params,
            )
        connection.exec_driver_sql(
            """
            UPDATE team_aliases SET team_id = %(c)s
            WHERE team_id = %(p)s AND normalized_alias NOT IN (
                SELECT normalized_alias FROM team_aliases WHERE team_id = %(c)s
            )
            """,
            params,
        )


def downgrade() -> None:
    # The split was the defect; re-creating it is not a recovery.
    pass
