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


def test_v25_moves_womens_fixtures_off_the_mens_row(tmp_path) -> None:
    """The reverse of v24: Liga MX Femenil fixtures ingested onto men's rows
    move to the women's club. Rows that already exist under the sources'
    inconsistent names are reused, never duplicated."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v25, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'split_femenil.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, cname in (("c-mx", "Liga MX"), ("c-mxf", "Liga MX Femenil")):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": cid, "n": cname},
            )
        for tid, name in (
            ("t-cruz", "Cruz Azul"),
            ("t-mty", "Monterrey"),
            ("t-mty-f", "C.F. Monterrey Femenil"),
            ("t-riv", "Rival"),
        ):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        for mid, cid, home, away, day in (
            ("m-men", "c-mx", "t-cruz", "t-riv", 1),      # men's fixture: must not move
            ("m-cruz-f", "c-mxf", "t-cruz", "t-riv", 2),  # women's on men's row, no women's row yet
            ("m-mty-f", "c-mxf", "t-mty", "t-riv", 3),    # women's on men's row, women's row exists
        ):
            connection.execute(
                text(
                    "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                    " VALUES (:i, :c, :h, :a, :k)"
                ),
                {"i": mid, "c": cid, "h": home, "a": away, "k": datetime(2026, 4, day, tzinfo=timezone.utc)},
            )

    with db_session.engine.begin() as connection:
        _migrate_to_v25(connection)

    with db_session.engine.begin() as connection:
        def home_of(match_id: str) -> str:
            return connection.execute(
                text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = :i"),
                {"i": match_id},
            ).scalar_one()

        assert home_of("m-men") == "Cruz Azul", "men's fixture must stay put"
        assert home_of("m-cruz-f") == "Cruz Azul Femenil", "women's row created"
        assert home_of("m-mty-f") == "C.F. Monterrey Femenil", "existing row reused"

        # The existing women's row must not have been duplicated.
        monterrey_rows = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name LIKE '%Monterrey%'")
        ).scalar_one()
    assert monterrey_rows == 2, "Monterrey + C.F. Monterrey Femenil, nothing minted"


def test_v25_leaves_a_womens_only_club_alone(tmp_path) -> None:
    """A club whose real name carries no gender marker and that plays only
    women's football (Washington Spirit) was never contaminated and must not
    be split."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v25, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'split_nwsl.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO competitions (id, name, is_placeholder) VALUES ('c-w', 'Liga MX Femenil', 0)")
        )
        for tid, name in (("t-ws", "Washington Spirit"), ("t-riv", "Rival")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-ws', 'c-w', 't-ws', 't-riv', :k)"
            ),
            {"k": datetime(2026, 4, 9, tzinfo=timezone.utc)},
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v25(connection)
        still = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = 'm-ws'")
        ).scalar_one()
    assert still == "Washington Spirit"


def test_v26_attaches_the_short_form_femenil_aliases(tmp_path) -> None:
    """The fixture feed says "Pachuca Femenil"; the row is called "C.F.
    Pachuca Femenil". Without an alias the lookup misses and the next ingest
    mints a duplicate club."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v26, run_migrations
    from app.models import tables  # noqa: F401
    from app.repositories.entity_repository import EntityRepository
    from app.services.normalization_service import NormalizationService

    db_session.configure_session(f"sqlite:///{tmp_path / 'femenil_alias_v26.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for tid, name in (("t-pac", "C.F. Pachuca Femenil"), ("t-atl", "Atlas Femenil")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )

    with db_session.engine.begin() as connection:
        _migrate_to_v26(connection)

    normalizer = NormalizationService()
    with db_session.SessionLocal() as session:
        repo = EntityRepository(session)
        found = repo.find_team_by_alias(
            "Pachuca Femenil", normalizer.normalize_team_name("Pachuca Femenil")
        )
        assert found is not None and found.name == "C.F. Pachuca Femenil"
        # A row whose name already matches gets a self-alias, so it resolves
        # by slug too rather than only by an exact-name hit.
        by_slug = repo.find_team_by_alias(
            "atlas femenil", normalizer.normalize_team_name("Atlas Femenil")
        )
        assert by_slug is not None and by_slug.name == "Atlas Femenil"

    # Idempotent: a second pass adds nothing.
    with db_session.engine.begin() as connection:
        before = connection.execute(text("SELECT COUNT(*) FROM team_aliases")).scalar_one()
        _migrate_to_v26(connection)
        after = connection.execute(text("SELECT COUNT(*) FROM team_aliases")).scalar_one()
    assert before == after


def test_v26_never_steals_an_alias_another_team_owns(tmp_path) -> None:
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v26, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'femenil_alias_owned.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for tid, name in (("t-pac", "C.F. Pachuca Femenil"), ("t-other", "Otro")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES ('a-x', 't-other', 'Pachuca Femenil', 'pachuca-femenil')"
            )
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v26(connection)
        owner = connection.execute(
            text("SELECT team_id FROM team_aliases WHERE alias = 'Pachuca Femenil'")
        ).scalar_one()
    assert owner == "t-other"


def test_v26_respects_the_unique_slug_constraint(tmp_path) -> None:
    """"América Femenil" and "CF América Femenil" normalize to the same slug,
    and uq_team_alias_normalized makes that unique. Attaching the short form
    must therefore make the self-alias a no-op, not a crash — this is what
    broke startup the first time v26 shipped."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v26, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'femenil_slug_clash.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO teams (id, name, is_placeholder) VALUES ('t-ame', 'CF América Femenil', 0)")
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v26(connection)

    with db_session.engine.begin() as connection:
        slugs = [
            row[0]
            for row in connection.execute(
                text("SELECT normalized_alias FROM team_aliases WHERE team_id = 't-ame'")
            ).fetchall()
        ]
    assert slugs == ["america-femenil"], slugs


def test_v27_folds_guide_placeholders_into_the_real_club(tmp_path) -> None:
    """The Progol guide names clubs in short form; when the short form does
    not resolve, slate promotion mints an empty placeholder beside the real
    row and the position is scored as if the club had no history."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v27, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'guide_placeholders.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO competitions (id, name, is_placeholder) VALUES ('c-mls', 'MLS', 0)")
        )
        for tid, name, ph in (
            ("t-ph", "Salt Lake", 1),
            ("t-real", "Real Salt Lake", 0),
            ("t-riv", "St. Louis City SC", 0),
        ):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, :p)"),
                {"i": tid, "n": name, "p": ph},
            )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-1', 'c-mls', 't-riv', 't-ph', :k)"
            ),
            {"k": datetime(2026, 8, 2, tzinfo=timezone.utc)},
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v27(connection)
        away = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.away_team_id WHERE m.id = 'm-1'")
        ).scalar_one()
    assert away == "Real Salt Lake"

    # The placeholder row survives — merges never delete, so nothing dangles.
    with db_session.engine.begin() as connection:
        survives = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name = 'Salt Lake'")
        ).scalar_one()
    assert survives == 1


def test_v27_leaves_a_placeholder_with_no_real_row_alone(tmp_path) -> None:
    """Aberdeen and Hearts have no real row behind them — no source covers
    Scottish football — so there is nothing to merge into and the migration
    must not invent one."""
    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v27, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'guide_scotland.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO teams (id, name, is_placeholder) VALUES ('t-abe', 'Aberdeen', 1)")
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v27(connection)
        rows = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name LIKE 'Aberdeen%'")
        ).scalar_one()
    assert rows == 1


def _seed_slate_with_kickoffs(connection, *, slate_id, draw_code, kickoffs):
    """Insert a slate whose positions carry the given kickoffs, 1-indexed."""
    from sqlalchemy import text

    connection.execute(
        text(
            "INSERT INTO competitions (id, name, is_placeholder) "
            f"VALUES ('c-{slate_id}', 'Brasileirao', 0)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO progol_slates (id, label, draw_code, week_type, is_archived, "
            "created_at, slate_version) VALUES "
            "(:id, :label, :code, 'midweek', 0, :created, 1)"
        ),
        {
            "id": slate_id,
            "label": draw_code,
            "code": draw_code,
            "created": kickoffs[0],
        },
    )
    for position, kickoff in enumerate(kickoffs, start=1):
        team_prefix = f"{slate_id}-{position}"
        connection.execute(
            text("INSERT INTO teams (id, name, is_placeholder) VALUES (:id, :name, 0)"),
            {"id": f"h-{team_prefix}", "name": f"Home {team_prefix}"},
        )
        connection.execute(
            text("INSERT INTO teams (id, name, is_placeholder) VALUES (:id, :name, 0)"),
            {"id": f"a-{team_prefix}", "name": f"Away {team_prefix}"},
        )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at) "
                f"VALUES (:id, 'c-{slate_id}', :home, :away, :kickoff)"
            ),
            {
                "id": f"m-{team_prefix}",
                "home": f"h-{team_prefix}",
                "away": f"a-{team_prefix}",
                "kickoff": kickoff,
            },
        )
        # `id` is an autoincrementing integer here, so it is left to the DB.
        connection.execute(
            text(
                "INSERT INTO progol_slate_matches (slate_id, match_id, position, is_knockout, "
                "knockout_source) VALUES (:slate, :match, :position, 0, 'auto')"
            ),
            {
                "slate": slate_id,
                "match": f"m-{team_prefix}",
                "position": position,
            },
        )


def test_v32_marks_fabricated_kickoff_ladders_and_spares_real_fixtures(tmp_path) -> None:
    """The promotion path fabricates `cierre + 12h` stepped an hour per
    position when no feed reported a pair. Those rows sat in `matches`
    indistinguishable from observed fixtures — printed as real kickoffs and
    adopted by the next slate's resolver. v32 marks them by their ladder shape,
    which is what still identifies PGM-806 after its cierre was corrected and
    its kickoffs no longer relate to it. A slate with real kickoffs forms no
    ladder and must come out untouched."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'placeholder_ladder.db'}")
    run_migrations(db_session.engine)

    base = datetime(2026, 7, 30, 11, 37, 37, tzinfo=timezone.utc)
    with db_session.engine.begin() as connection:
        # PGM-806's shape: a perfect hourly ladder, and a cierre it no longer
        # relates to (the operator corrected it two days earlier).
        _seed_slate_with_kickoffs(
            connection,
            slate_id="s-ladder",
            draw_code="PGM-806",
            kickoffs=[base + timedelta(hours=i) for i in range(9)],
        )
        # Real feed times: irregular gaps, two fixtures sharing a slot.
        _seed_slate_with_kickoffs(
            connection,
            slate_id="s-real",
            draw_code="PG-REAL",
            kickoffs=[
                datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 1, 17, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 1, 17, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc),
            ],
        )
        # Two positions an hour apart is an ordinary broadcast pairing, not
        # evidence of fabrication — below the ladder threshold.
        _seed_slate_with_kickoffs(
            connection,
            slate_id="s-short",
            draw_code="PG-SHORT",
            kickoffs=[base, base + timedelta(hours=1)],
        )

    from app.db.migrations import _migrate_to_v32

    with db_session.engine.begin() as connection:
        _migrate_to_v32(connection)

    with db_session.engine.connect() as connection:
        marked = {
            row[0]
            for row in connection.execute(
                text("SELECT id FROM matches WHERE is_placeholder = 1")
            )
        }

    assert len(marked) == 9
    assert all(match_id.startswith("m-s-ladder") for match_id in marked)


def test_v33_folds_the_argentine_splits_toward_the_live_feed_name(tmp_path) -> None:
    """ARG.csv writes "Argentinos Jrs", which today resolves to a one-match
    placeholder sitting beside the club's real 21-match row. Ingesting before
    the merge would pile a season onto the wrong row.

    The surviving row must be the one the LIVE feeds write, even when it is
    currently the thinner of the two — otherwise the next ingest reopens the
    split."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v33, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'argentine_splits.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO competitions (id, name, is_placeholder)"
                " VALUES ('c-arg', 'Argentinian Primera Division', 0)"
            )
        )
        for tid, name, ph in (
            ("t-jrs", "Argentinos Jrs", 1),
            ("t-juniors", "Argentinos Juniors", 0),
            ("t-inst-acc", "Instituto ACC", 0),
            ("t-inst", "Instituto", 0),
            ("t-rival", "Sarmiento", 0),
        ):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, :p)"),
                {"i": tid, "n": name, "p": ph},
            )
        for mid, home, away, day in (
            ("m-jrs", "t-rival", "t-jrs", 26),
            ("m-acc", "t-inst-acc", "t-rival", 27),
        ):
            connection.execute(
                text(
                    "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                    " VALUES (:i, 'c-arg', :h, :a, :k)"
                ),
                {"i": mid, "h": home, "a": away, "k": datetime(2026, 7, day, tzinfo=timezone.utc)},
            )

    with db_session.engine.begin() as connection:
        _migrate_to_v33(connection)
        away_name = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.away_team_id WHERE m.id = 'm-jrs'")
        ).scalar_one()
        home_name = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = 'm-acc'")
        ).scalar_one()

    assert away_name == "Argentinos Juniors"
    # Direction follows the live feed name, not the richer row.
    assert home_name == "Instituto"

    # Merges never delete: both source rows survive so nothing dangles.
    with db_session.engine.begin() as connection:
        survivors = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name IN ('Argentinos Jrs', 'Instituto ACC')")
        ).scalar_one()
    assert survivors == 2


def test_v33_is_idempotent_and_spares_absent_rows(tmp_path) -> None:
    """Re-running must not move anything a second time, and a database that
    never had the split rows must come out untouched."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v33, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'argentine_idempotent.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO competitions (id, name, is_placeholder)"
                " VALUES ('c-arg', 'Argentinian Primera Division', 0)"
            )
        )
        for tid, name in (("t-a", "Argentinos Juniors"), ("t-b", "Sarmiento")):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-1', 'c-arg', 't-a', 't-b', :k)"
            ),
            {"k": datetime(2026, 7, 23, tzinfo=timezone.utc)},
        )

    for _ in range(2):
        with db_session.engine.begin() as connection:
            _migrate_to_v33(connection)

    with db_session.engine.begin() as connection:
        home = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = 'm-1'")
        ).scalar_one()
        teams = connection.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
    assert home == "Argentinos Juniors"
    assert teams == 2


def test_v34_corrects_only_the_guide_mislabelled_pgm806_positions(tmp_path) -> None:
    """The promotion path infers a competition from shared team history when
    no fixture resolves. For PGM-806 that put a Copa Sudamericana playoff under
    "Brasileirao". v34 replaces the inference with what the official LN guide
    states — and must leave the positions the guide already agrees with alone."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v34, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'pgm806_labels.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, name, ph in (
            ("c-ph", "Progol Concurso 806", 1),
            ("c-bra", "Brasileirao", 0),
            ("c-lib", "Copa Libertadores", 0),
            ("c-sud", "Copa Sudamericana", 0),
            ("c-exp", "Liga de Expansion MX", 0),
            ("c-arg", "Argentinian Primera Division", 0),
        ):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, :p)"),
                {"i": cid, "n": name, "p": ph},
            )
        for tid in ("t-a", "t-b"):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :i, 0)"), {"i": tid}
            )
        connection.execute(
            text(
                "INSERT INTO progol_slates (id, label, draw_code, week_type, is_archived, slate_version, created_at)"
                " VALUES ('s-806', 'Progol 806', 'PGM-806', 'midweek', 1, 1, :c)"
            ),
            {"c": datetime(2026, 7, 28, tzinfo=timezone.utc)},
        )
        # position -> competition it currently sits under
        seeded = {2: "c-ph", 3: "c-bra", 5: "c-bra", 6: "c-ph", 8: "c-lib", 9: "c-ph"}
        for position, cid in seeded.items():
            connection.execute(
                text(
                    "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                    " VALUES (:i, :c, 't-a', 't-b', :k)"
                ),
                {"i": f"m-{position}", "c": cid, "k": datetime(2026, 7, 30, 11 + position, tzinfo=timezone.utc)},
            )
            connection.execute(
                text(
                    "INSERT INTO progol_slate_matches (slate_id, match_id, position, is_knockout, knockout_source)"
                    " VALUES ('s-806', :m, :p, 0, 'auto')"
                ),
                {"m": f"m-{position}", "p": position},
            )

    def competitions_by_position():
        with db_session.engine.begin() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT sm.position, c.name FROM progol_slate_matches sm"
                        " JOIN matches m ON m.id = sm.match_id"
                        " JOIN competitions c ON c.id = m.competition_id"
                        " WHERE sm.slate_id = 's-806'"
                    )
                ).all()
            )

    before = competitions_by_position()
    with db_session.engine.begin() as connection:
        _migrate_to_v34(connection)
    after = competitions_by_position()

    assert after[2] == "Liga de Expansion MX"
    assert after[5] == "Copa Sudamericana"
    assert after[6] == "Copa Sudamericana"
    assert after[8] == "Argentinian Primera Division"
    assert after[9] == "Argentinian Primera Division"
    # Position 3 already agreed with the guide and must be untouched.
    assert after[3] == before[3] == "Brasileirao"

    # Idempotent: a second run changes nothing.
    with db_session.engine.begin() as connection:
        _migrate_to_v34(connection)
    assert competitions_by_position() == after


def test_v34_leaves_other_slates_alone(tmp_path) -> None:
    """The corrections are anchored by (draw_code, position); a different
    concurso holding the same pair under the same competition must not move."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v34, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'other_slate.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, name in (("c-bra", "Brasileirao"), ("c-sud", "Copa Sudamericana")):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": cid, "n": name},
            )
        for tid in ("t-a", "t-b"):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :i, 0)"), {"i": tid}
            )
        connection.execute(
            text(
                "INSERT INTO progol_slates (id, label, draw_code, week_type, is_archived, slate_version, created_at)"
                " VALUES ('s-805', 'Progol 805', 'PGM-805', 'midweek', 1, 1, :c)"
            ),
            {"c": datetime(2026, 7, 21, tzinfo=timezone.utc)},
        )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-5', 'c-bra', 't-a', 't-b', :k)"
            ),
            {"k": datetime(2026, 7, 23, tzinfo=timezone.utc)},
        )
        connection.execute(
            text(
                "INSERT INTO progol_slate_matches (slate_id, match_id, position, is_knockout, knockout_source)"
                " VALUES ('s-805', 'm-5', 5, 0, 'auto')"
            )
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v34(connection)
        name = connection.execute(
            text("SELECT c.name FROM matches m JOIN competitions c ON c.id = m.competition_id WHERE m.id = 'm-5'")
        ).scalar_one()
    assert name == "Brasileirao"


def test_v35_folds_manchester_city_toward_the_league_history_row(tmp_path) -> None:
    """v30 left this pair alone because the rows share no competition and the
    position was blocked by policy anyway. With the Champions League now
    carrying a published benchmark, thin history is what remains — so the
    21-match UCL row folds into the 114-match E0 one."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.db import session as db_session
    from app.db.migrations import _migrate_to_v35, run_migrations
    from app.models import tables  # noqa: F401

    db_session.configure_session(f"sqlite:///{tmp_path / 'man_city.db'}")
    run_migrations(db_session.engine)

    with db_session.engine.begin() as connection:
        for cid, name in (("c-e0", "E0"), ("c-ucl", "UEFA Champions League")):
            connection.execute(
                text("INSERT INTO competitions (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": cid, "n": name},
            )
        for tid, name in (
            ("t-short", "Man City"),
            ("t-long", "Manchester City FC"),
            ("t-rival", "Real Madrid"),
        ):
            connection.execute(
                text("INSERT INTO teams (id, name, is_placeholder) VALUES (:i, :n, 0)"),
                {"i": tid, "n": name},
            )
        connection.execute(
            text(
                "INSERT INTO matches (id, competition_id, home_team_id, away_team_id, kickoff_at)"
                " VALUES ('m-ucl', 'c-ucl', 't-long', 't-rival', :k)"
            ),
            {"k": datetime(2026, 8, 1, tzinfo=timezone.utc)},
        )

    with db_session.engine.begin() as connection:
        _migrate_to_v35(connection)
        home = connection.execute(
            text("SELECT t.name FROM matches m JOIN teams t ON t.id = m.home_team_id WHERE m.id = 'm-ucl'")
        ).scalar_one()
        survivors = connection.execute(
            text("SELECT COUNT(*) FROM teams WHERE name = 'Manchester City FC'")
        ).scalar_one()

    assert home == "Man City"
    # Merges never delete; the source row stays so nothing dangles.
    assert survivors == 1
