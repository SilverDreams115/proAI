"""Continental/domestic split merge — River Plate and Atlético Nacional.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-25

Revision 0021 deliberately skipped two pairs whose names collide only after
prefix stripping, because a wrong merge there would fuse genuinely different
clubs. Both were then resolved from match evidence:

* "River Plate" (Argentinian Primera Division only) and "CA River Plate"
  (Copa Libertadores only) never meet, never play on the same day, and the
  Libertadores group on the CA row is River Plate Argentina's real 2025 group.
* "CDC Atlético Nacional" is the Colombian club's legal name (Corporación
  Deportiva Club Atlético Nacional), so it is the same side as
  "Atlético Nacional".

"Club Nacional" is still NOT merged into "Club Nacional de Football": the two
ran simultaneous 2024 Libertadores qualifying ties against different opponents
one day apart, which proves they are different clubs. See the note above
``_CONTINENTAL_SPLIT_MERGES`` in ``app/db/migrations.py``.

Data migration only — no schema DDL. ``_migrate_to_v22`` is the apply path;
``SCHEMA_VERSION`` is bumped to 22.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v22) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: merge provenance is not recorded. Mirrors 0014/0021.
    pass
