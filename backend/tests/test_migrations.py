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


def test_v23_renormalizes_femenil_aliases(tmp_path) -> None:
    """Aliases stored while "femenil" was a stopword keep the collapsed slug
    until v23 re-derives them. Until then a men's lookup can land on the
    women's row, because find_team_by_alias matches on normalized_alias."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v23, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'femenil_alias.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for tid, name in (("t-w", "Tigres UANL Femenil"), ("t-m", "Tigres")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        # The collapsed slug the old normalizer produced.
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES ('a-w', 't-w', 'Tigres UANL Femenil', 'tigres-uanl')"
            )
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v23(connection)

    with db_session.engine.begin() as connection:
        slug = connection.execute(
            text("SELECT normalized_alias FROM team_aliases WHERE id = 'a-w'")
        ).scalar_one()
    assert slug == "tigres-uanl-femenil"

    # Idempotent: a second pass recomputes the same value.
    with db_session.engine.begin() as connection:
        _migrate_to_v23(connection)
        again = connection.execute(
            text("SELECT normalized_alias FROM team_aliases WHERE id = 'a-w'")
        ).scalar_one()
    assert again == slug


def test_v23_skips_an_alias_whose_new_slug_another_team_owns(tmp_path) -> None:
    """The unique index on normalized_alias must never be violated: if some
    other team already holds the recomputed slug, leave the row alone."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v23, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'femenil_clash.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for tid, name in (("t-a", "Club America Femenil"), ("t-b", "Otro")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES ('a-1', 't-a', 'Club America Femenil', 'club-america')"
            )
        )
        # Another team already owns the slug v23 would compute.
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES ('a-2', 't-b', 'ocupado', 'america-femenil')"
            )
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v23(connection)
        untouched = connection.execute(
            text("SELECT normalized_alias FROM team_aliases WHERE id = 'a-1'")
        ).scalar_one()
    assert untouched == "club-america"


def test_v24_splits_a_merged_row_by_competition(tmp_path) -> None:
    """The femenil collision fused three clubs onto "Barcelona Femenino".
    v24 peels each competition onto the club it belongs to, creates the
    target rows that never existed because of the collision, and leaves the
    women's fixtures untouched."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v24, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'split_barcelona.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, cname in (
            ("c-sp1", "SP1"),
            ("c-lib", "Copa Libertadores"),
            ("c-ucw", "UEFA Champions League Femenina"),
        ):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": cid, "n": cname},
            )
        for tid, name in (
            ("t-merged", "Barcelona Femenino"),
            ("t-madrid", "Real Madrid"),
            ("t-boca", "CA Boca Juniors"),
            ("t-lyon", "Lyonnes Femenino"),
        ):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        for mid, cid, home, away, day in (
            ("m-sp1", "c-sp1", "t-merged", "t-madrid", 10),
            ("m-lib", "c-lib", "t-boca", "t-merged", 11),
            ("m-ucw", "c-ucw", "t-merged", "t-lyon", 12),
        ):
            connection.execute(
                text(
                    "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                    " VALUES (:i, :c, :h, :a, :k)"
                ),
                {"i": mid, "c": cid, "h": home, "a": away, "k": datetime(2026, 3, day, tzinfo=timezone.utc)},
            )

    with db_session.engine.begin() as connection:
        _migrate_to_v24(connection)

    with db_session.engine.begin() as connection:
        def team_of(match_id: str, side: str) -> str:
            return connection.execute(
                text(f"SELECT t.name FROM matches m JOIN teams t ON t.id = m.{side} WHERE m.id = :i"),
                {"i": match_id},
            ).scalar_one()

        # Men's league fixture moved onto a freshly created men's row.
        assert team_of("m-sp1", "home_team_id") == "Barcelona"
        # Libertadores fixture moved onto the Ecuadorian club.
        assert team_of("m-lib", "away_team_id") == "Barcelona SC"
        # The women's fixture is untouched.
        assert team_of("m-ucw", "home_team_id") == "Barcelona Femenino"

        country = connection.execute(
            text("SELECT country FROM teams WHERE name = 'Barcelona SC'")
        ).scalar_one()
        assert country == "Ecuador"

    # Idempotent: nothing left in those competitions to move.
    with db_session.engine.begin() as connection:
        _migrate_to_v24(connection)
        still = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = 'm-sp1'")
        ).scalar_one()
    assert still == "Barcelona"


def test_v24_is_a_noop_when_the_merged_row_is_absent(tmp_path) -> None:
    """A fresh database has none of these rows; the migration must not
    invent teams or fail."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v24, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'split_noop.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        _migrate_to_v24(connection)
        created = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name IN ('Barcelona', 'Barcelona SC')")
        ).scalar_one()
    assert created == 0
