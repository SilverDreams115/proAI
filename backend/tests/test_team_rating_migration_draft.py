"""R2: the migration DRAFT exists, is inert, and declares both tables.

The draft lives OUTSIDE ``alembic/versions/`` on purpose so it is neither
autodiscovered by alembic nor counted by ``migration_audit_errors`` — which
would otherwise break app startup and the whole test suite.
"""

from __future__ import annotations

from pathlib import Path

from app.db.migrations import SCHEMA_VERSION
from app.db.migrations import migration_audit_errors

_DRAFT = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "drafts"
    / "0019_team_rating_persistence.py"
)


def test_draft_file_exists_outside_versions():
    assert _DRAFT.exists()
    versions_copy = _DRAFT.parents[1] / "versions" / _DRAFT.name
    # Must NOT have been placed into versions/ (that would auto-apply / break audit).
    assert not versions_copy.exists()


def test_draft_declares_both_tables_and_constraints():
    text = _DRAFT.read_text()
    assert '"team_rating_runs"' in text
    assert '"team_rating_snapshots"' in text
    # key columns
    for column in (
        "algorithm_version",
        "config_json",
        "input_checksum",
        "output_checksum",
        "status",
        "namespace",
        "rating",
        "confidence_bucket",
        "competitions_seen_json",
    ):
        assert column in text, column
    # constraints / indexes from the spec
    assert "uq_team_rating_snapshot_identity" in text
    assert "ck_team_rating_run_status" in text
    assert "ck_team_rating_snapshot_namespace" in text
    assert "ck_team_rating_snapshot_confidence" in text
    assert "ix_team_rating_snapshots_team_namespace" in text
    assert 'revision = "0019"' in text
    assert 'down_revision = "0018"' in text


def test_draft_does_not_break_runtime_migration_audit():
    # The draft must not bump the apparent alembic head: SCHEMA_VERSION stays
    # aligned with the latest *versions/* revision, so the audit passes.
    assert SCHEMA_VERSION == 18
    assert migration_audit_errors() == []
