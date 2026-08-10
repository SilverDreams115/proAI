"""Consolidate fixtures that exist twice because feeds disagree on kickoff.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-10

No schema changes. Data-only migration. See _migrate_to_v37 in
app/db/migrations.py.

`uq_matches_fixture_identity` keys on the exact kickoff, so one real match
reported by two sources an hour apart is two rows, and everything arriving
afterwards splits between them. Production carries 41 such groups: 14 with a
result on both sides — those were double counted in every form window until
the result dedupe started collapsing them — and 27 with a hollow twin.

The hollow twin is the expensive half. PGM-797 holds five positions pointing at
rows carrying the EVIDENCE while their twins carry the RESULTS, which is why
that slate reports 1/9 canonical coverage with eight results already in the
database. The direction matters: the slate's row is not the empty one, so
relinking the slate to the twin would trade one loss for another. The fix moves
the dependents onto the row the slate already uses, not the link.

Survivor per cluster: a slate-linked row first, then a real row over a
fabricated one, then more results, then the earliest kickoff. Pairs are unioned
into clusters so a fixture recorded three times folds once. Every dependent
table is re-pointed — results, live results, stat snapshots, availability,
evidence, feature snapshots, predictions and source documents — skipping rows
that would collide with a unique key the survivor already satisfies. Losers are
marked is_placeholder so no resolver returns them. Nothing is deleted.

The companion change is in SlateRepository.upsert_slate, which now falls back
to EntityRepository.find_match_near_identity before creating a row, so
promotion stops minting these. This revision only cleans up what the old
behaviour produced.
"""

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
