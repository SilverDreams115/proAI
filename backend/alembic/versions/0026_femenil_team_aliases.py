"""Give the Liga MX Femenil rows the aliases their fixture feed needs.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-27

The women's rows carry the names the results sources produced — "C.F.
Pachuca Femenil", "Club Universidad Nacional Femenil", "CF América
Femenil" — while the fixture feed emits the short form: "Pachuca
Femenil", "Pumas UNAM Femenil", "América Femenil".

``find_team_by_alias`` resolves on an exact ``TeamModel.name`` match or an
alias slug, and the eleven rows 0025 created were inserted as raw SQL with
no alias row at all. Checked against the live database before writing
this: seven of the fifteen club names in the 2026-2027 fixture list
resolved to nothing, so enabling that season would have minted a second
row for each — the duplication 0021 already had to clean up once.

Attaches the ten differing short forms explicitly, and backfills a
self-alias for every women's row lacking one so a club is reachable by its
own normalized slug rather than depending on punctuation and accents
matching character for character.

Data migration only, no schema DDL, no deletes; an alias another team
already owns is left alone rather than moved. ``_migrate_to_v26`` is the
apply path; ``SCHEMA_VERSION`` is bumped to 26.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v26) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: dropping the aliases would re-open the duplication.
    pass
