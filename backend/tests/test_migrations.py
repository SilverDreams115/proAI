from sqlalchemy import inspect


def test_run_migrations_creates_operational_indexes(tmp_path) -> None:
    from app.db import session as db_session
    from app.db.migrations import SCHEMA_VERSION
    from app.db.migrations import run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'migration_indexes.db'}")
    run_migrations(db_session.engine)

    inspector = inspect(db_session.engine)
    indexes_by_table = {
        table_name: {index["name"] for index in inspector.get_indexes(table_name)}
        for table_name in (
            "ingestion_runs",
            "source_documents",
            "source_health_checks",
            "scheduled_ingestion_jobs",
            "model_training_runs",
        )
    }

    assert "ix_ingestion_runs_source_status_started_at" in indexes_by_table["ingestion_runs"]
    assert "ix_source_documents_source_captured_at" in indexes_by_table["source_documents"]
    assert "ix_source_health_checks_source_checked_at" in indexes_by_table["source_health_checks"]
    assert "ix_scheduled_ingestion_jobs_active_next_run_at" in indexes_by_table["scheduled_ingestion_jobs"]
    assert "ix_model_training_runs_model_trained_at" in indexes_by_table["model_training_runs"]
    slate_columns = {column["name"] for column in inspector.get_columns("progol_slates")}
    assert "registration_closes_at" in slate_columns
    assert "is_archived" in slate_columns
    assert "ticket_recommendation_snapshots" in inspector.get_table_names()
    ticket_indexes = {index["name"] for index in inspector.get_indexes("ticket_recommendation_snapshots")}
    assert "ix_ticket_recommendation_snapshots_slate_id" in ticket_indexes

    with db_session.engine.connect() as connection:
        version = connection.exec_driver_sql("SELECT version FROM schema_migrations LIMIT 1").scalar_one()
    assert version == SCHEMA_VERSION


def test_alembic_configuration_is_present() -> None:
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]

    assert (backend_root / "alembic.ini").exists()
    assert (backend_root / "alembic" / "env.py").exists()
    assert (backend_root / "alembic" / "versions" / "0005_ticket_recommendation_snapshots.py").exists()


def test_runtime_schema_version_matches_alembic_review_revision() -> None:
    from app.db.migrations import migration_audit_errors

    assert migration_audit_errors() == []
