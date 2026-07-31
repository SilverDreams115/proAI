"""Fold the three split club rows in the Argentine Primera.

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-31

TheSportsDB's free tier caps ``eventsday.php`` at three events per call, so
the Argentine league arrives with holes: Barracas Central's 2026-07-25
fixture never came through, which is one of the two reasons PG-2344
position 10 is blocked. The repair is to ingest football-data.co.uk's
ARG.csv, which carries no per-response cap.

That CSV writes club names in short form, and three of them land on the
wrong row today:

    Argentinos Jrs (1, placeholder)  ->  Argentinos Juniors (21)
    Instituto ACC (13, last 2024)    ->  Instituto (8, last 2026)
    Gimnasia LP (10, last 2024)      ->  Gimnasia y Esgrima de La Plata (7, last 2026)

"Argentinos Jrs" is the live trap: ARG.csv writes exactly that string, so
ingesting before this merge would have piled a full season onto a one-match
placeholder while the club's real history sat beside it. The other two are
temporal splits — one source named the club until 2024, another names it
now — leaving each side holding half the record.

Direction is chosen by the name the LIVE feeds write, not by which row is
currently richer: the surviving row must be the one the next ingest
resolves to, or the split reopens on the following run. Hence Instituto ACC
(13 matches) folds into Instituto (8), not the reverse.

All three pairs sit inside "Argentinian Primera Division" — the
shared-competition check required before any merge.

Manchester City FC (21, UCL) / Man City (114, E0) stays excluded, for the
reasons 0030 recorded: the rows share no competition, and position 5 is
blocked by unclassified_competition rather than by thin history, so the
merge would repair nothing.
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_MERGES = (
    ("Argentinos Jrs", "Argentinos Juniors"),
    ("Instituto ACC", "Instituto"),
    ("Gimnasia LP", "Gimnasia y Esgrima de La Plata"),
)


def upgrade() -> None:
    connection = op.get_bind()
    for split_name, canonical_name in _MERGES:
        split = connection.exec_driver_sql(
            "SELECT id FROM teams WHERE name = %(n)s LIMIT 1", {"n": split_name}
        ).fetchone()
        canonical = connection.exec_driver_sql(
            "SELECT id FROM teams WHERE name = %(n)s LIMIT 1", {"n": canonical_name}
        ).fetchone()
        if split is None or canonical is None or split[0] == canonical[0]:
            continue
        params = {"p": split[0], "c": canonical[0]}
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
