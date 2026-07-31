"""Fold the Manchester City split that 0030 deferred.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-31

No schema changes. Data-only migration. See _migrate_to_v35 in
app/db/migrations.py.

0030 examined this pair and deliberately excluded it, for two reasons. Both
have since stopped holding:

* "its rows are split ACROSS competitions" — Man City (114, E0) and Manchester
  City FC (21, UEFA Champions League) share no competition, so the
  shared-competition guard had nothing to check. The evidence here is not name
  similarity: the UCL row's opponents are Real Madrid, Inter, PSG, Juventus,
  Dortmund and Leverkusen — the senior men's Manchester City and no one else.
  The LN guide for concurso 2344 writes the club both ways itself.
* "blocked by unclassified_competition rather than by thin history, so the
  merge would not have unblocked it" — true until the walk-forward verdict
  published on 2026-07-31 gave the Champions League an audited benchmark (365
  fixtures, XGBoost brier 0.5598 vs heuristic 0.5721) and moved it off
  `unclassified`. Thin history is now what holds position 5 down: the slate
  points at the 21-match row while 114 matches, four inside the active form
  window, sit on the other.

Direction follows the richer row and the feed that keeps writing it — FD-UK's
E0 emits "Man City" every season. Normalization pins added alongside this
revision make the UCL feed resolve there too, so the split cannot reopen.

composition_hash is untouched: it fingerprints the promotion payload before
entity resolution, not the DB's team rows.
"""

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
