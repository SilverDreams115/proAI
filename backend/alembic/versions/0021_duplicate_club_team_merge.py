"""Duplicate club team merge — consolidate rows split by ingestion naming.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-25

Two ingestion sources name the same club differently: football-data.org writes
the legal name ("CR Flamengo", "SE Palmeiras", "EC Vitória") while TheSportsDB
writes the short form ("Flamengo", "Palmeiras", "Vitoria"). Each affected club
therefore has two ``teams`` rows holding half of its result history, which made
fixtures look like they had no recent form while the twin row was being updated
weekly — enough to trip the ``_has_insufficient_data`` gate and block an entire
slate.

Data migration only — no schema DDL. The runtime migrator
(``app/db/migrations.py``) is the real apply path: ``SCHEMA_VERSION`` is bumped
to 21 and ``_migrate_to_v21`` merges the pairs listed in
``_DUPLICATE_CLUB_MERGES`` via ``_merge_team_into``. This file exists so the
change is reviewable in the alembic history; ``migration_audit_errors``
requires ``max(versions/) == SCHEMA_VERSION``.

Nothing is deleted: the source row stays, emptied of references, so no history
dangles. Rows that would collide with an existing fixture identity or an alias
the canonical team already owns are skipped rather than overwritten.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — no schema DDL.
    # The runtime migration (_migrate_to_v21) handles this at startup.
    pass


def downgrade() -> None:
    # Intentionally a no-op: reverting an entity merge would require knowing
    # which of the merged references originally belonged to the source row,
    # and that provenance is not recorded. Mirrors revision 0014.
    pass
