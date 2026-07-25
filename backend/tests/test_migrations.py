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


def test_v21_merges_duplicate_club_rows(tmp_path) -> None:
    """A club split across two team rows by rival naming conventions must end
    up with a single row owning every match — otherwise each row holds half the
    result history and the recent-form gate blocks the fixture."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v21, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'merge_clubs.db'}")
    run_migrations(db_session.engine)

    kickoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with db_session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO competitions (id, name, is_placeholder) VALUES ('c1', 'Brasileirao', 0)")
        )
        # "Flamengo" (TheSportsDB) and "CR Flamengo" (football-data.org).
        for team_id, name in (("t-short", "Flamengo"), ("t-legal", "CR Flamengo"), ("t-rival", "Rival")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": team_id, "n": name},
            )
        # One match under each naming convention, different dates so the merge
        # cannot be refused as a duplicate fixture identity.
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m1', 'c1', 't-short', 't-rival', :k)"
            ),
            {"k": kickoff},
        )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m2', 'c1', 't-rival', 't-legal', :k)"
            ),
            {"k": kickoff.replace(day=2)},
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v21(connection)

    with db_session.engine.connect() as connection:
        home = connection.execute(text("SELECT home_team_id FROM matches WHERE id='m1'")).scalar_one()
        away = connection.execute(text("SELECT away_team_id FROM matches WHERE id='m2'")).scalar_one()
        short_refs = connection.execute(
            text("SELECT count(*) FROM matches WHERE home_team_id='t-short' OR away_team_id='t-short'")
        ).scalar_one()
        rows = connection.execute(text("SELECT count(*) FROM teams WHERE id='t-short'")).scalar_one()

    # Both matches now hang off the canonical row the live feed writes...
    assert home == "t-legal"
    assert away == "t-legal"
    assert short_refs == 0
    # ...and the emptied row survives, so nothing in history dangles.
    assert rows == 1


def test_distinct_nacional_clubs_are_never_merged() -> None:
    """"Nacional" collides across six real clubs. Whoever extends the merge
    lists must not fold them together on name similarity: "Club Nacional"
    (Paraguay) played "CDC Atlético Nacional" home and away, and ran a 2024
    Libertadores qualifying tie in parallel with "Club Nacional de Football"
    (Montevideo)."""
    from app.db.migrations import _CONTINENTAL_SPLIT_MERGES, _DUPLICATE_CLUB_MERGES

    pairs = _DUPLICATE_CLUB_MERGES + _CONTINENTAL_SPLIT_MERGES
    forbidden = {"Club Nacional", "Club Nacional de Football", "CSCyD El Nacional",
                 "Club Nacional Potosí", "Club Universidad Nacional Femenil"}
    for source, canonical in pairs:
        assert source not in forbidden, f"{source} is a distinct club, not a duplicate"
        assert canonical not in forbidden, f"{canonical} is a distinct club, not a merge target"
    # The Colombian side is the one legitimately consolidated.
    assert ("CDC Atlético Nacional", "Atlético Nacional") in _CONTINENTAL_SPLIT_MERGES


def test_v22_merges_the_continental_split_rows(tmp_path) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v22, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'merge_continental.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, cname in (("c-dom", "Argentinian Primera Division"), ("c-int", "Copa Libertadores")):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": cid, "n": cname},
            )
        for tid, name in (("t-dom", "River Plate"), ("t-int", "CA River Plate"), ("t-riv", "Rival")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-int', 'c-int', 't-int', 't-riv', :k)"
            ),
            {"k": datetime(2025, 4, 3, tzinfo=timezone.utc)},
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v22(connection)

    with db_session.engine.connect() as connection:
        home = connection.execute(text("SELECT home_team_id FROM matches WHERE id='m-int'")).scalar_one()
    # The Libertadores row folds into the domestic row the live feed updates.
    assert home == "t-dom"


def test_v21_is_idempotent(tmp_path) -> None:
    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v21, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'merge_idempotent.db'}")
    run_migrations(db_session.engine)
    # A second and third pass on a database with none of the pairs present
    # must be a silent no-op rather than an error.
    with db_session.engine.begin() as connection:
        _migrate_to_v21(connection)
        _migrate_to_v21(connection)


def test_alembic_configuration_is_present() -> None:
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]

    assert (backend_root / "alembic.ini").exists()
    assert (backend_root / "alembic" / "env.py").exists()
    assert (backend_root / "alembic" / "versions" / "0005_ticket_recommendation_snapshots.py").exists()


def test_runtime_schema_version_matches_alembic_review_revision() -> None:
    from app.db.migrations import migration_audit_errors

    assert migration_audit_errors() == []
