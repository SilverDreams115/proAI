"""R2 APPLY: the team rating migration is promoted, wired, and applied.

Revision 0019 now lives in ``alembic/versions/`` (no longer a draft),
``SCHEMA_VERSION`` is 19, ``migration_audit_errors`` passes, and a freshly
migrated DB has both team_rating_* tables.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from app.db.migrations import SCHEMA_VERSION
from app.db.migrations import migration_audit_errors

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "0019_team_rating_persistence.py"
_OLD_DRAFT = _VERSIONS.parent / "drafts" / "0019_team_rating_persistence.py"


def test_migration_promoted_into_versions():
    assert _MIGRATION.exists()
    # The draft must no longer exist (it was moved, not duplicated).
    assert not _OLD_DRAFT.exists()


def test_migration_declares_both_tables_and_constraints():
    text = _MIGRATION.read_text()
    assert '"team_rating_runs"' in text
    assert '"team_rating_snapshots"' in text
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
    assert "uq_team_rating_snapshot_identity" in text
    assert "ck_team_rating_run_status" in text
    assert "ck_team_rating_snapshot_namespace" in text
    assert "ck_team_rating_snapshot_confidence" in text
    assert "ix_team_rating_snapshots_team_namespace" in text
    assert 'revision = "0019"' in text
    assert 'down_revision = "0018"' in text


def test_schema_version_bumped_and_audit_passes():
    assert SCHEMA_VERSION == 19
    assert migration_audit_errors() == []


def test_fresh_migration_creates_team_rating_tables(tmp_path):
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'v19.db'}")
    run_migrations(db_session.engine)

    insp = inspect(db_session.engine)
    names = set(insp.get_table_names())
    assert "team_rating_runs" in names
    assert "team_rating_snapshots" in names

    with db_session.engine.connect() as conn:
        version = conn.execute(text("SELECT version FROM schema_migrations")).scalar_one()
    assert int(version) == 19
