"""Consolidate two club rows that two ingestion sources split.

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-28

PG-2344 came out with 5 of its 14 positions blocked. Two of them were
blocked for a reason that was entirely repairable: the slate resolved to
a placeholder team row while the club's real history sat on a second row
that a different ingestion source had created.

    Manchester United (3 matches, E0)  ->  Man United (114, E0)
    Seattle (3, MLS)                   ->  Seattle Sounders (87, MLS)

So position 6 ("Man United vs Atletico de Madrid") and position 13
("Portland Timbers vs Seattle") were scored as if those clubs had three
matches of history each, and came out insufficient_data_anchors.

Both pairs share a competition, which is the check the note above
``_CONTINENTAL_SPLIT_MERGES`` requires before any merge is allowed.

Manchester City is deliberately excluded. Its rows are split ACROSS
competitions (Man City in E0, Manchester City FC in the Champions
League), which is exactly the case that note warns must never be driven
by name similarity, and its position is blocked by
unclassified_competition rather than by thin history — the merge would
not have unblocked it.

Aberdeen/Hearts (position 14) and Riestra/Barracas (position 10) are not
repairable here: neither has a second row to merge. The first is Scottish
football, which no configured source ingests; the second is genuinely
thin Argentine coverage.
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_MERGES = (
    ("Manchester United", "Man United"),
    ("Seattle", "Seattle Sounders"),
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
