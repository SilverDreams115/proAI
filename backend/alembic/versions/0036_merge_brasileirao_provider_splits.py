"""Fold the Brasileirão club rows that two feeds split in half.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-10

No schema changes. Data-only migration. See _migrate_to_v36 in
app/db/migrations.py.

football-data.org and TheSportsDB spell Brazilian clubs differently and
neither spelling was pinned as an alias of the other, so fifteen clubs carry
two parallel rows — one per feed, each with half the history.

Surfaced by PGM-808 position 9. The slate resolved to `Cruzeiro` (116 matches,
last played 2026-07-30) while `Cruzeiro EC` (66 matches, last played
2026-08-09) held what the results feed keeps writing. Flamengo is split the
same way but asymmetrically: that side of the fixture landed on the rich row,
`CR Flamengo`. The model compared a fully-fed team against one missing two
rounds, read the gap as 9.7 days of extra rest, and flipped the pick from 1 to
2 while promoting the position from REVISAR to LISTO — an artefact of the
split, not a read on the match.

Membership is established by fixture identity, not name similarity: two rows
for one club cannot hold different opponents at the same kickoff. Every pair
folded here shows zero conflicts and positive fixture overlap once opponents
are themselves resolved through the same mapping — the second pass matters,
since the raw comparison misreports Cruzeiro's 13 "conflicts" as different
opponents when they are Chapecoense vs Chapecoense AF, Gremio vs Grêmio FBPA
and so on: one match, rival written under its own split pair.

Unlike previous merges this also retires the duplicate row (renamed, flagged
placeholder) and pins its spelling as an alias. `_merge_team_into` alone is
not durable — the resolver matches on `teams.name` too, so the losing feed
would re-resolve to the duplicate and reopen the split on the next jornada.

Excluded for want of evidence: Botafogo / Botafogo-SP and América Mineiro /
Athletic Club-MG (different clubs — the first pair holds two same-kickoff
fixtures against different opponents), the 2024-only Atletico MG/PR/GO CSV
rows (no overlap with any modern row), and the one-match placeholder
Inter Porto Alegre.

composition_hash is untouched: it fingerprints the promotion payload before
entity resolution, not the DB's team rows.
"""

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
