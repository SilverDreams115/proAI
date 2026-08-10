from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
import uuid

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.base import Base

SCHEMA_VERSION = 37
POSTGRES_MIGRATION_LOCK_ID = 791796
ALEMBIC_VERSION_PATTERN = re.compile(r"^0*(?P<version>\d+)_.*\.py$")


def migration_audit_errors() -> list[str]:
    """Return configuration errors between runtime migrations and Alembic review files."""
    alembic_versions = _alembic_schema_versions()
    if not alembic_versions:
        return ["No Alembic revision files were found for migration review."]
    latest_alembic_version = max(alembic_versions)
    if latest_alembic_version != SCHEMA_VERSION:
        return [
            "Runtime SCHEMA_VERSION "
            f"({SCHEMA_VERSION}) does not match latest Alembic revision ({latest_alembic_version})."
        ]
    return []


def _alembic_schema_versions() -> list[int]:
    versions_dir = _alembic_versions_dir()
    if versions_dir is None:
        return []
    versions: list[int] = []
    for path in versions_dir.glob("*.py"):
        match = ALEMBIC_VERSION_PATTERN.match(path.name)
        if match:
            versions.append(int(match.group("version")))
    return versions


def _alembic_versions_dir() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[2] / "alembic" / "versions",
        Path.cwd() / "backend" / "alembic" / "versions",
        Path("/app/backend/alembic/versions"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def run_migrations(engine: Engine) -> None:
    audit_errors = migration_audit_errors()
    if audit_errors:
        raise RuntimeError(
            "Migration audit failed; refusing to start: " + "; ".join(audit_errors)
        )
    with _migration_lock(engine):
        _run_migrations_unlocked(engine)


def _run_migrations_unlocked(engine: Engine) -> None:
    inspector = inspect(engine)
    if "schema_migrations" not in inspector.get_table_names():
        _bootstrap_schema(engine)
        return

    with engine.begin() as connection:
        current = connection.execute(text("SELECT version FROM schema_migrations LIMIT 1")).scalar_one_or_none()
        current_version = int(current or 0)
        if current_version < 1:
            Base.metadata.create_all(bind=connection)
            current_version = 1
        if current_version < 2:
            _migrate_to_v2(connection)
            current_version = 2
        if current_version < 3:
            _migrate_to_v3(connection)
            current_version = 3
        if current_version < 4:
            _migrate_to_v4(connection)
            current_version = 4
        if current_version < 5:
            _migrate_to_v5(connection)
            current_version = 5
        if current_version < 6:
            _migrate_to_v6(connection)
            current_version = 6
        if current_version < 7:
            _migrate_to_v7(connection)
            current_version = 7
        if current_version < 8:
            _migrate_to_v8(connection)
            current_version = 8
        if current_version < 9:
            _migrate_to_v9(connection)
            current_version = 9
        if current_version < 10:
            _migrate_to_v10(connection)
            current_version = 10
        if current_version < 11:
            _migrate_to_v11(connection)
            current_version = 11
        if current_version < 12:
            _migrate_to_v12(connection)
            current_version = 12
        if current_version < 13:
            _migrate_to_v13(connection)
            current_version = 13
        if current_version < 14:
            _migrate_to_v14(connection)
            current_version = 14
        if current_version < 15:
            _migrate_to_v15(connection)
            current_version = 15
        if current_version < 16:
            _migrate_to_v16(connection)
            current_version = 16
        if current_version < 17:
            _migrate_to_v17(connection)
            current_version = 17
        if current_version < 18:
            _migrate_to_v18(connection)
            current_version = 18
        if current_version < 20:
            _migrate_to_v20(connection)
            current_version = 20
        if current_version < 21:
            _migrate_to_v21(connection)
            current_version = 21
        if current_version < 22:
            _migrate_to_v22(connection)
            current_version = 22
        if current_version < 23:
            _migrate_to_v23(connection)
            current_version = 23
        if current_version < 24:
            _migrate_to_v24(connection)
            current_version = 24
        if current_version < 25:
            _migrate_to_v25(connection)
            current_version = 25
        if current_version < 26:
            _migrate_to_v26(connection)
            current_version = 26
        if current_version < 27:
            _migrate_to_v27(connection)
            current_version = 27
        if current_version < 28:
            _migrate_to_v28(connection)
            current_version = 28
        if current_version < 29:
            _migrate_to_v29(connection)
            current_version = 29
        if current_version < 30:
            _migrate_to_v30(connection)
            current_version = 30
        if current_version < 31:
            _migrate_to_v31(connection)
            current_version = 31
        if current_version < 32:
            _migrate_to_v32(connection)
            current_version = 32
        if current_version < 33:
            _migrate_to_v33(connection)
            current_version = 33
        if current_version < 34:
            _migrate_to_v34(connection)
            current_version = 34
        if current_version < 35:
            _migrate_to_v35(connection)
            current_version = 35
        if current_version < 36:
            _migrate_to_v36(connection)
            current_version = 36
        if current_version < 37:
            _migrate_to_v37(connection)
            current_version = 37
        connection.execute(text("UPDATE schema_migrations SET version = :version"), {"version": current_version})


@contextmanager
def _migration_lock(engine: Engine):
    if engine.dialect.name == "sqlite":
        database_path = engine.url.database
        if not database_path or database_path == ":memory:":
            yield
            return
        lock_path = Path(database_path).resolve().with_suffix(".migration.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": POSTGRES_MIGRATION_LOCK_ID})
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POSTGRES_MIGRATION_LOCK_ID},
            )


def _bootstrap_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        _migrate_to_v2(connection)
        _migrate_to_v3(connection)
        _migrate_to_v4(connection)
        _migrate_to_v5(connection)
        _migrate_to_v6(connection)
        _migrate_to_v7(connection)
        _migrate_to_v8(connection)
        _migrate_to_v9(connection)
        _migrate_to_v10(connection)
        _migrate_to_v11(connection)
        _migrate_to_v12(connection)
        _migrate_to_v13(connection)
        _migrate_to_v14(connection)
        _migrate_to_v15(connection)
        _migrate_to_v16(connection)
        _migrate_to_v17(connection)
        _migrate_to_v18(connection)
        _migrate_to_v20(connection)
        _migrate_to_v21(connection)
        _migrate_to_v22(connection)
        _migrate_to_v23(connection)
        _migrate_to_v24(connection)
        _migrate_to_v25(connection)
        _migrate_to_v26(connection)
        _migrate_to_v27(connection)
        _migrate_to_v28(connection)
        _migrate_to_v29(connection)
        _migrate_to_v30(connection)
        _migrate_to_v31(connection)
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER NOT NULL)"))
        has_row = connection.execute(text("SELECT 1 FROM schema_migrations LIMIT 1")).scalar_one_or_none()
        if has_row is None:
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": SCHEMA_VERSION},
            )


def _migrate_to_v2(connection) -> None:
    dialect_name = connection.engine.dialect.name
    if dialect_name == "sqlite":
        _deduplicate_sqlite_rows(connection)

    unique_indexes = [
        (
            "uq_matches_fixture_identity",
            "matches",
            "competition_id, home_team_id, away_team_id, kickoff_at",
        ),
        (
            "uq_team_stat_snapshot_identity",
            "team_stat_snapshots",
            "team_id, source_id, captured_at, stat_type",
        ),
        (
            "uq_match_stat_snapshot_identity",
            "match_stat_snapshots",
            "match_id, source_id, captured_at, stat_type",
        ),
        (
            "uq_match_result_identity",
            "match_results",
            "match_id, source_id, played_at",
        ),
        (
            "uq_team_player_identity",
            "team_players",
            "team_id, player_id",
        ),
        (
            "uq_player_availability_identity",
            "player_availability",
            "match_id, team_id, player_name, status, category, source_id, captured_at",
        ),
        (
            "uq_progol_slate_position",
            "progol_slate_matches",
            "slate_id, position",
        ),
        (
            "uq_progol_slate_match",
            "progol_slate_matches",
            "slate_id, match_id",
        ),
        (
            "uq_team_alias_normalized",
            "team_aliases",
            "normalized_alias",
        ),
        (
            "uq_competition_alias_normalized",
            "competition_aliases",
            "normalized_alias",
        ),
    ]
    for index_name, table_name, columns in unique_indexes:
        connection.execute(
            text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})")
        )


def _migrate_to_v3(connection) -> None:
    operational_indexes = [
        (
            "ix_ingestion_runs_source_status_started_at",
            "ingestion_runs",
            "source_id, status, started_at",
        ),
        (
            "ix_source_documents_source_captured_at",
            "source_documents",
            "source_id, captured_at",
        ),
        (
            "ix_source_health_checks_source_checked_at",
            "source_health_checks",
            "source_id, checked_at",
        ),
        (
            "ix_scheduled_ingestion_jobs_active_next_run_at",
            "scheduled_ingestion_jobs",
            "is_active, next_run_at",
        ),
        (
            "ix_model_training_runs_model_trained_at",
            "model_training_runs",
            "model_name, trained_at",
        ),
    ]
    for index_name, table_name, columns in operational_indexes:
        connection.execute(
            text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})")
        )


def _migrate_to_v4(connection) -> None:
    dialect_name = connection.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    boolean_default = "false" if dialect_name == "postgresql" else "0"
    _add_column_if_missing(
        connection,
        "progol_slates",
        "registration_closes_at",
        f"registration_closes_at {timestamp_type}",
    )
    _add_column_if_missing(
        connection,
        "progol_slates",
        "is_archived",
        f"is_archived BOOLEAN NOT NULL DEFAULT {boolean_default}",
    )


def _migrate_to_v5(connection) -> None:
    dialect_name = connection.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    text_type = "TEXT"
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS ticket_recommendation_snapshots ("
            "id VARCHAR(36) PRIMARY KEY, "
            "slate_id VARCHAR(36) NOT NULL, "
            f"generated_at {timestamp_type} NOT NULL, "
            "model_version VARCHAR(120) NOT NULL, "
            f"payload_json {text_type} NOT NULL DEFAULT '{{}}', "
            "FOREIGN KEY(slate_id) REFERENCES progol_slates (id)"
            ")"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_recommendation_snapshots_slate_id "
            "ON ticket_recommendation_snapshots (slate_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_recommendation_snapshots_generated_at "
            "ON ticket_recommendation_snapshots (generated_at)"
        )
    )


def _migrate_to_v6(connection) -> None:
    dialect_name = connection.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    text_type = "TEXT"
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS progol_slate_proposals ("
            "id VARCHAR(36) PRIMARY KEY, "
            "draw_code VARCHAR(64) NOT NULL, "
            "week_type VARCHAR(32) NOT NULL DEFAULT 'weekend', "
            "source_name VARCHAR(120) NOT NULL, "
            "source_url VARCHAR(500) NOT NULL, "
            f"registration_closes_at {timestamp_type}, "
            f"payload_json {text_type} NOT NULL DEFAULT '{{}}', "
            "status VARCHAR(32) NOT NULL DEFAULT 'observed', "
            "observations INTEGER NOT NULL DEFAULT 1, "
            f"first_seen_at {timestamp_type} NOT NULL, "
            f"last_seen_at {timestamp_type} NOT NULL, "
            "promoted_slate_id VARCHAR(36), "
            "FOREIGN KEY(promoted_slate_id) REFERENCES progol_slates (id)"
            ")"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_progol_proposal_source "
            "ON progol_slate_proposals (draw_code, source_url)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_progol_slate_proposals_draw_code "
            "ON progol_slate_proposals (draw_code)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_progol_slate_proposals_status "
            "ON progol_slate_proposals (status)"
        )
    )


def _migrate_to_v7(connection) -> None:
    """Add is_placeholder flag to teams and competitions.

    Placeholder rows are created when slate promotion can't resolve a
    fixture to a real team/competition (e.g., a Liga MX side the PDF
    names with a short alias we haven't ingested yet). Marking them
    explicitly lets `find_team_by_alias` skip them in favor of a real
    row once it lands, instead of having the placeholder win the lookup
    by name match — which is the bug we hit with "Tampico" vs
    "Tampico Madero".
    """
    _add_column_if_missing(
        connection,
        "teams",
        "is_placeholder",
        "is_placeholder BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        connection,
        "competitions",
        "is_placeholder",
        "is_placeholder BOOLEAN NOT NULL DEFAULT FALSE",
    )
    # Backfill: pre-existing "Progol Concurso NNNN" competitions are
    # placeholder by construction.
    connection.execute(
        text(
            "UPDATE competitions SET is_placeholder = TRUE "
            "WHERE name LIKE 'Progol Concurso %'"
        )
    )


def _migrate_to_v8(connection) -> None:
    """Extend predictions with the audit fields the prediction service
    actually produces (competition_readiness, blocked_reason, anchors).

    Until v8 the predictions table existed in the schema but no code
    wrote to it. Starting in v8 the prediction service persists one row
    per scored match so that blocked / unclassified outcomes have a
    durable audit trail beyond log rotation.
    """
    _add_column_if_missing(
        connection,
        "predictions",
        "competition_readiness",
        "competition_readiness VARCHAR(32)",
    )
    _add_column_if_missing(
        connection,
        "predictions",
        "blocked_reason",
        "blocked_reason VARCHAR(120)",
    )
    _add_column_if_missing(
        connection,
        "predictions",
        "anchors_json",
        "anchors_json TEXT NOT NULL DEFAULT '{}'",
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_predictions_match_generated "
            "ON predictions (match_id, generated_at)"
        )
    )


def _migrate_to_v9(connection) -> None:
    """Mark progol_slate_matches positions that are knockout fixtures.

    Knockout / elimination fixtures (Champions League final, Liga MX
    Liguilla final, etc.) advance a winner, but Progol still grades the
    position on the 90-minute result — a tie decided by penalties is an
    official "X". The flag only trims the model's draw mass; it never
    removes it. The slate-match level (not the match level) is the right
    scope: the same fixture pair could be a friendly one week and a
    final the next.
    """
    _add_column_if_missing(
        connection,
        "progol_slate_matches",
        "is_knockout",
        "is_knockout BOOLEAN NOT NULL DEFAULT FALSE",
    )


def _migrate_to_v10(connection) -> None:
    """Track slate fixture composition to prevent stale ticket snapshots.

    composition_hash is a SHA-256 of the ordered fixture list (draw_code +
    week_type + sorted positions + lower-cased team names + kickoff ISO +
    competition name). When the same draw_code arrives with different
    fixtures the hash changes, slate_version is incremented, and all prior
    is_valid=True ticket snapshots for that slate are flipped to is_valid=False
    so they can never be surfaced as current recommendations.

    The invalidated_at / invalidation_reason columns provide an audit trail
    without deleting the historical rows.
    """
    dialect_name = connection.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    _add_column_if_missing(
        connection,
        "progol_slates",
        "composition_hash",
        "composition_hash VARCHAR(64)",
    )
    _add_column_if_missing(
        connection,
        "progol_slates",
        "slate_version",
        "slate_version INTEGER NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(
        connection,
        "ticket_recommendation_snapshots",
        "composition_hash",
        "composition_hash VARCHAR(64)",
    )
    _add_column_if_missing(
        connection,
        "ticket_recommendation_snapshots",
        "is_valid",
        "is_valid BOOLEAN NOT NULL DEFAULT TRUE",
    )
    _add_column_if_missing(
        connection,
        "ticket_recommendation_snapshots",
        "invalidated_at",
        f"invalidated_at {timestamp_type}",
    )
    _add_column_if_missing(
        connection,
        "ticket_recommendation_snapshots",
        "invalidation_reason",
        "invalidation_reason VARCHAR(120)",
    )


def _migrate_to_v13(connection) -> None:
    """Add result_source_priority to sources.

    Lower value = higher authority when resolving conflicts between multiple
    sources that provide a result for the same match. Default 50 keeps
    all existing sources at equal priority — operators bump official providers
    down to e.g. 10 to guarantee they win any multi-source disagreement.
    """
    _add_column_if_missing(
        connection,
        "sources",
        "result_source_priority",
        "result_source_priority INTEGER NOT NULL DEFAULT 50",
    )


def _migrate_to_v12(connection) -> None:
    """Create progol_jornada_scores — one scoring record per slate version.

    Stores simple accuracy, Brier score, per-confidence-band hit rates and
    ticket recommendation accuracy keyed by (slate_id, composition_hash).
    The unique constraint ensures repeated compute() calls update the same
    row rather than appending duplicates.
    """
    dialect_name = connection.engine.dialect.name
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    boolean_default_false = "false" if dialect_name == "postgresql" else "0"
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS progol_jornada_scores ("
            "id VARCHAR(36) PRIMARY KEY, "
            "slate_id VARCHAR(36) NOT NULL, "
            "draw_code VARCHAR(64) NOT NULL, "
            "week_type VARCHAR(32) NOT NULL, "
            "composition_hash VARCHAR(64), "
            "slate_version INTEGER, "
            "total_matches INTEGER NOT NULL DEFAULT 0, "
            "matches_with_results INTEGER NOT NULL DEFAULT 0, "
            "simple_hits INTEGER NOT NULL DEFAULT 0, "
            "simple_hit_rate REAL, "
            "ticket_hits INTEGER, "
            "ticket_hit_rate REAL, "
            "brier_score_avg REAL, "
            "high_confidence_hits INTEGER NOT NULL DEFAULT 0, "
            "high_confidence_total INTEGER NOT NULL DEFAULT 0, "
            "medium_confidence_hits INTEGER NOT NULL DEFAULT 0, "
            "medium_confidence_total INTEGER NOT NULL DEFAULT 0, "
            "low_confidence_hits INTEGER NOT NULL DEFAULT 0, "
            "low_confidence_total INTEGER NOT NULL DEFAULT 0, "
            "blocked_hits INTEGER NOT NULL DEFAULT 0, "
            "blocked_total INTEGER NOT NULL DEFAULT 0, "
            "details_json TEXT NOT NULL DEFAULT '[]', "
            f"computed_at {timestamp_type} NOT NULL, "
            f"is_complete BOOLEAN NOT NULL DEFAULT {boolean_default_false}, "
            "FOREIGN KEY(slate_id) REFERENCES progol_slates (id)"
            ")"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_jornada_score_slate_version "
            "ON progol_jornada_scores (slate_id, composition_hash)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_jornada_scores_slate_id "
            "ON progol_jornada_scores (slate_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_jornada_scores_draw_code "
            "ON progol_jornada_scores (draw_code)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_jornada_scores_computed_at "
            "ON progol_jornada_scores (computed_at)"
        )
    )


def _migrate_to_v11(connection) -> None:
    """Link prediction audit rows to the slate that triggered them.

    Adds slate_id (FK to progol_slates.id), composition_hash, and
    slate_version to the predictions table. All three are nullable so
    legacy rows (scored before v11) remain valid. The composite index on
    (slate_id, match_id, generated_at) supports the query pattern
    "give me all predictions for this slate version."
    """
    _add_column_if_missing(connection, "predictions", "slate_id", "slate_id VARCHAR(36)")
    _add_column_if_missing(connection, "predictions", "composition_hash", "composition_hash VARCHAR(64)")
    _add_column_if_missing(connection, "predictions", "slate_version", "slate_version INTEGER")
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_predictions_slate_id ON predictions (slate_id)")
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_predictions_slate_match_generated "
            "ON predictions (slate_id, match_id, generated_at)"
        )
    )


def _add_column_if_missing(connection, table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(connection)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def _table_exists(connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _deduplicate_sqlite_rows(connection) -> None:
    duplicate_cleanup_statements = [
        """
        DELETE FROM matches
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM matches
            GROUP BY competition_id, home_team_id, away_team_id, kickoff_at
        )
        """,
        """
        DELETE FROM team_stat_snapshots
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM team_stat_snapshots
            GROUP BY team_id, source_id, captured_at, stat_type
        )
        """,
        """
        DELETE FROM match_stat_snapshots
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM match_stat_snapshots
            GROUP BY match_id, source_id, captured_at, stat_type
        )
        """,
        """
        DELETE FROM match_results
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM match_results
            GROUP BY match_id, source_id, played_at
        )
        """,
        """
        DELETE FROM team_players
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM team_players
            GROUP BY team_id, player_id
        )
        """,
        """
        DELETE FROM player_availability
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM player_availability
            GROUP BY match_id, team_id, player_name, status, category, source_id, captured_at
        )
        """,
        """
        DELETE FROM progol_slate_matches
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM progol_slate_matches
            GROUP BY slate_id, position
        )
        """,
        """
        DELETE FROM team_aliases
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM team_aliases
            GROUP BY normalized_alias
        )
        """,
        """
        DELETE FROM competition_aliases
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM competition_aliases
            GROUP BY normalized_alias
        )
        """,
    ]
    for statement in duplicate_cleanup_statements:
        connection.execute(text(statement))


def _migrate_to_v14(connection) -> None:
    """Merge duplicate national-team placeholder entities into canonical English ones.

    Phase 8 coverage fix: the Progol PDF uses Spanish team names ("Croacia",
    "Túnez", "Bosnia", etc.). When the fixture resolver fails to find an
    existing TSDB-ingested match for a pair it creates a new placeholder
    TeamModel for each Spanish name.  TheSportsDB International Friendlies
    data uses English names ("Croatia", "Tunisia", "Bosnia-Herzegovina", …),
    creating a second real entity.  The feature service queries results by
    team_id and therefore finds zero history for the Spanish placeholder —
    triggering confidence_band=blocked / insufficient_data_anchors on every
    national-team friendly.

    This migration re-points all match rows that reference a Spanish
    placeholder to the canonical English entity, then moves the placeholder's
    aliases to the canonical so future entity resolution resolves them there.

    The composition_hash stored in progol_slates is UNCHANGED: it was
    computed from the PDF payload strings at ingest time and is not derived
    from team entity IDs.

    Pairs:
      Bosnia           → Bosnia-Herzegovina
      Chequia          → Czech Republic
      Croacia          → Croatia
      Nueva Zelanda    → New Zealand
      República De Corea → South Korea
      Túnez            → Tunisia

    Note: Bélgica and Turkey are not placeholder entities and are not merged
    here; their low result count is a data-coverage gap that requires
    additional TSDB sources (e.g. UEFA Nations League 2024-25 season).

    Idempotent: all UPDATEs use WHERE conditions that are false once the
    migration has already run.  Safe on both SQLite (tests) and PostgreSQL
    (production).
    """
    # (placeholder_team_name, canonical_team_name)
    # names are used only for logging; IDs are not hard-coded so the
    # migration survives being run on a fresh DB (where neither entity exists).
    pairs = [
        ("Bosnia", "Bosnia-Herzegovina"),
        ("Chequia", "Czech Republic"),
        ("Croacia", "Croatia"),
        ("Nueva Zelanda", "New Zealand"),
        ("República De Corea", "South Korea"),
        ("Túnez", "Tunisia"),
    ]
    for placeholder_name, canonical_name in pairs:
        _merge_team_into(connection, placeholder_name, canonical_name)


def _merge_team_into(connection, placeholder_name: str, canonical_name: str) -> None:
    """Move all match references from one team row to another.

    Re-points ``matches.home_team_id`` / ``matches.away_team_id`` and moves
    ``team_aliases``, skipping rows that would violate a unique constraint
    (a duplicate fixture identity, or an alias the canonical team already
    owns). The source row is never deleted, so nothing in history dangles.

    Used both for national-team placeholders (v14/v16) and for club rows that
    two ingestion sources split under different naming conventions (v21).

    No-op when either entity does not exist (fresh DB / test).
    """
    placeholder_row = connection.execute(
        text("SELECT id FROM teams WHERE name = :name LIMIT 1"),
        {"name": placeholder_name},
    ).fetchone()
    canonical_row = connection.execute(
        text("SELECT id FROM teams WHERE name = :name LIMIT 1"),
        {"name": canonical_name},
    ).fetchone()
    if placeholder_row is None or canonical_row is None:
        return

    placeholder_id = placeholder_row[0]
    canonical_id = canonical_row[0]
    if placeholder_id == canonical_id:
        return  # already merged

    # Re-point home-team references (only where no duplicate unique-key conflict).
    connection.execute(
        text("""
            UPDATE matches
            SET home_team_id = :canonical_id
            WHERE home_team_id = :placeholder_id
              AND NOT EXISTS (
                  SELECT 1 FROM matches m2
                  WHERE m2.id != matches.id
                    AND m2.competition_id = matches.competition_id
                    AND m2.home_team_id = :canonical_id
                    AND m2.away_team_id = matches.away_team_id
                    AND m2.kickoff_at = matches.kickoff_at
              )
        """),
        {"placeholder_id": placeholder_id, "canonical_id": canonical_id},
    )

    # Re-point away-team references.
    connection.execute(
        text("""
            UPDATE matches
            SET away_team_id = :canonical_id
            WHERE away_team_id = :placeholder_id
              AND NOT EXISTS (
                  SELECT 1 FROM matches m2
                  WHERE m2.id != matches.id
                    AND m2.competition_id = matches.competition_id
                    AND m2.home_team_id = matches.home_team_id
                    AND m2.away_team_id = :canonical_id
                    AND m2.kickoff_at = matches.kickoff_at
              )
        """),
        {"placeholder_id": placeholder_id, "canonical_id": canonical_id},
    )

    # Move team aliases from placeholder to canonical.
    # Some normalized aliases may already exist on canonical — skip those
    # to avoid unique constraint violations.
    #
    # PostgreSQL: use a NOT EXISTS subquery.
    # SQLite:     same approach works.
    connection.execute(
        text("""
            UPDATE team_aliases
            SET team_id = :canonical_id
            WHERE team_id = :placeholder_id
              AND NOT EXISTS (
                  SELECT 1 FROM team_aliases ta2
                  WHERE ta2.team_id = :canonical_id
                    AND ta2.normalized_alias = team_aliases.normalized_alias
              )
        """),
        {"placeholder_id": placeholder_id, "canonical_id": canonical_id},
    )


def _migrate_to_v15(connection) -> None:
    """Re-link matches whose competition_id is a 'Progol Concurso NNNN' placeholder
    to the canonical 'International Friendlies' competition when such a canonical
    competition exists and there is no conflicting row at the same
    (comp, home, away, kickoff_at) tuple.

    Root cause: the slate fixture resolver resolves matches against the DB.  When it
    cannot find an existing International Friendlies match for a national-team pair
    (usually because both teams were still Spanish-name placeholders), it creates a new
    MatchModel under the 'Progol Concurso NNNN' placeholder competition.  After v14
    merges the placeholder team entities, the teams are correct but the competition
    remains the placeholder.  That causes competition_operating_policy to return
    'context_only' instead of the 'ready' policy that International Friendlies carries.

    Idempotent: the NOT EXISTS guard prevents double-updates.
    No-op on fresh DB or when no International Friendlies competition is registered.
    """
    canonical_comp = connection.execute(
        text("SELECT id FROM competitions WHERE name = 'International Friendlies' LIMIT 1")
    ).fetchone()
    if canonical_comp is None:
        return
    canonical_comp_id = canonical_comp[0]

    connection.execute(
        text("""
            UPDATE matches
            SET competition_id = :canonical_comp_id
            WHERE competition_id IN (
                SELECT id FROM competitions
                WHERE name LIKE 'Progol Concurso %'
                  AND is_placeholder = TRUE
            )
            AND NOT EXISTS (
                SELECT 1 FROM matches m2
                WHERE m2.id != matches.id
                  AND m2.competition_id = :canonical_comp_id
                  AND m2.home_team_id = matches.home_team_id
                  AND m2.away_team_id = matches.away_team_id
                  AND m2.kickoff_at = matches.kickoff_at
            )
        """),
        {"canonical_comp_id": canonical_comp_id},
    )


def _migrate_to_v16(connection) -> None:
    """Merge 'Re P. Corea' placeholder entity into canonical 'South Korea'.

    Root cause: the Progol Media Semana PDF uses the abbreviated form
    "Re P. Corea" for South Korea. Before this fix the normalization
    service lacked an alias entry for the alias-key "re p corea", so the
    entity resolver created a new placeholder TeamModel instead of linking
    to the TSDB-ingested South Korea entity. The feature service then
    found zero recent results for "Re P. Corea", triggering
    confidence_band=blocked due to insufficient data anchors — even though
    South Korea has valid recent history ingested under the canonical name.

    This migration follows the same pattern as _migrate_to_v14: it
    re-points match rows that reference the placeholder to the canonical
    entity, then moves the placeholder's alias to the canonical so future
    entity resolution resolves there directly.

    The normalization_service alias fix ("re p corea" → "south-korea",
    "rep corea" → "south-korea", "korea rep" → "south-korea") prevents new
    placeholders from being created on re-ingestion.

    Idempotent: the NOT EXISTS guards prevent double-updates.
    No-op on fresh DB or when either entity does not exist.
    """
    _merge_team_into(connection, "Re P. Corea", "South Korea")


def _migrate_to_v18(connection) -> None:
    """Add predictions.sanity_audit_json: the full guardrail trace.

    One additive, nullable JSON column. Pre-sanity rows stay NULL (we do
    not invent a decision that was never taken). The existing
    home/draw/away_probability columns are untouched and remain the
    MODEL-adjusted backtesting source — this column never overwrites them.

    Idempotent via _add_column_if_missing.
    """
    _add_column_if_missing(
        connection,
        "predictions",
        "sanity_audit_json",
        "sanity_audit_json TEXT",
    )


def _migrate_to_v17(connection) -> None:
    """Create match_live_results: live/partial/final observations per source.

    Kept separate from match_results so the canonical-final store and
    CanonicalResultRepository are never polluted by in-progress scores.
    Idempotent via CREATE TABLE IF NOT EXISTS; no-op when already present.
    """
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS match_live_results (
                id VARCHAR(36) PRIMARY KEY,
                match_id VARCHAR(36) NOT NULL REFERENCES matches(id),
                source_id VARCHAR(36) NOT NULL REFERENCES sources(id),
                status VARCHAR(16) NOT NULL DEFAULT 'scheduled',
                home_goals INTEGER,
                away_goals INTEGER,
                result_code VARCHAR(1),
                minute INTEGER,
                is_final BOOLEAN NOT NULL DEFAULT FALSE,
                observed_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_match_live_identity UNIQUE (match_id, source_id)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_match_live_results_match_id "
            "ON match_live_results (match_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_match_live_results_source_id "
            "ON match_live_results (source_id)"
        )
    )


# (source_name, canonical_name) club rows that two ingestion sources split
# under different naming conventions. football-data.org writes the legal club
# name ("CR Flamengo", "SE Palmeiras", "EC Vitória"); TheSportsDB writes the
# short form ("Flamengo", "Palmeiras", "Vitoria"). Every affected club ended up
# with two team rows, each holding half the result history — which is why a
# fixture can look like it has no recent form while the other row is being
# updated weekly.
#
# The canonical target is always the row the LIVE ingestion currently writes
# (the one with the most recent result), never simply the one with more rows:
# merging into a stale name would let the next ingestion run split it again.
#
# Deliberately NOT merged, because they are different clubs that only collide
# once prefixes are stripped:
#   * "Atlético Nacional" (Colombia) vs "Club Nacional" (Uruguay/Paraguay)
#   * "CA River Plate" vs "River Plate" — plausibly the same Argentine club
#     split by source, but River Plate Montevideo would collide identically,
#     so it needs an operator decision rather than a guess.
_DUPLICATE_CLUB_MERGES: tuple[tuple[str, str], ...] = (
    # Brazil — short form -> legal name written by the live FD-ORG feed.
    ("Flamengo", "CR Flamengo"),
    ("Palmeiras", "SE Palmeiras"),
    ("Vitoria", "EC Vitória"),
    ("Bahia", "EC Bahia"),
    ("Atlético Mineiro", "CA Mineiro"),
    ("CR Vasco da Gama", "Vasco da Gama"),
    ("CA Paranaense", "Athletico Paranaense"),
    ("EC Juventude", "Juventude"),
    # Mexico / USA / Spain — punctuation and rename variants.
    ("Morelia", "Atlético Morelia"),
    ("L.A. Galaxy", "LA Galaxy"),
    ("La Coruna", "Deportivo de La Coruña"),
    ("Ath. Bilbao", "Ath Bilbao"),
    # Argentina / Chile / Colombia.
    ("OHiggins", "O'Higgins"),
    ("CA Vélez Sarsfield", "Velez Sarsfield"),
    ("Newells Old Boys", "Newell's Old Boys"),
    ("Platense", "CA Platense"),
    ("CA Bucaramanga", "Atlético Bucaramanga"),
    ("CA Central Córdoba", "Central Córdoba de Santiago del Estero"),
    # Placeholder rows created from truncated slate names, now that their
    # fixtures have been relinked to the canonical club.
    ("Paranaense", "Athletico Paranaense"),
    ("Talleres", "CA Talleres"),
    ("Atl. Tucumán", "Atletico Tucuman"),
    ("Central Córdoba", "Central Córdoba de Santiago del Estero"),
)


# The two pairs v21 deliberately left alone, resolved afterwards from match
# evidence rather than from the names. Both turned out to be one club whose
# domestic rows and continental rows were written by different feeds:
#
#   * "River Plate" holds only Argentinian Primera Division fixtures and
#     "CA River Plate" only Copa Libertadores ones; they never meet and never
#     play on the same day, and the 2025 Libertadores group on the CA row
#     (Universitario, Independiente del Valle, Barcelona) is River Plate
#     Argentina's real group.
#   * "Corporación Deportiva Club Atlético Nacional" is the Colombian club's
#     legal name, so "CDC Atlético Nacional" (Libertadores only) and
#     "Atlético Nacional" (Colombian Primera A only) are the same side.
#
# Still NOT merged, and this is the reason to keep the note: "Nacional"
# collides across six real clubs — Montevideo ("Club Nacional de Football"),
# Colombia, Paraguay ("Club Nacional"), Ecuador ("CSCyD El Nacional"), Bolivia
# ("Club Nacional Potosí") and Liga MX Femenil's Universidad Nacional.
# "Club Nacional" and "Club Nacional de Football" ran two SIMULTANEOUS 2024
# Libertadores qualifying ties against different opponents, one day apart, so
# they are provably different clubs; "Club Nacional" also played "CDC Atlético
# Nacional" home and away. Name similarity alone must never drive a merge here.
_CONTINENTAL_SPLIT_MERGES: tuple[tuple[str, str], ...] = (
    ("CA River Plate", "River Plate"),
    ("CDC Atlético Nacional", "Atlético Nacional"),
)


# (source team, competition, target team, target country). Each row moves the
# matches a merged team row holds in ONE competition onto the club they
# actually belong to, creating the target row when it does not exist yet.
#
# "Barcelona Femenino" accumulated 168 matches while "femenil"/"femenino" were
# team stopwords: every "Barcelona" normalized to `barcelona`, and
# find_team_by_alias prefers an alias hit over a name hit, so three different
# clubs landed on the women's row. The split is read off the opponents and is
# not a judgement call:
#   * SP1 — Real Madrid, Sevilla, Athletic, Betis, Valencia...  -> men's club
#   * UEFA Champions League — Bayern, Inter, Chelsea, PSG...    -> men's club
#   * Copa Libertadores — Boca, River, Corinthians, El Nacional -> Barcelona SC
#     (Ecuador), the only Barcelona that plays that competition
#   * UEFA Champions League Femenina — vs Lyonnes Femenino      -> stays put
#
# "Tigres UANL Femenil" holds one men's Liga MX fixture (Tijuana vs Tigres,
# 2026-07-17) by the same mechanism; the men's row already exists.
_MERGED_TEAM_SPLITS: tuple[tuple[str, str, str, str | None], ...] = (
    ("Barcelona Femenino", "SP1", "Barcelona", "Spain"),
    ("Barcelona Femenino", "UEFA Champions League", "Barcelona", "Spain"),
    ("Barcelona Femenino", "Copa Libertadores", "Barcelona SC", "Ecuador"),
    ("Tigres UANL Femenil", "Liga MX", "Tigres", None),
)


def _split_team_matches_by_competition(
    connection,
    source_name: str,
    competition_name: str,
    target_name: str,
    target_country: str | None,
) -> None:
    """Move one team's matches in one competition onto another team row.

    The inverse of ``_merge_team_into``: that helper folds two rows together,
    this one peels a competition's worth of fixtures off a row that absorbed
    clubs it never was. Creates the target team when absent — unlike the merge
    helpers, the correct row may simply not exist yet, because the collision
    prevented it from ever being created.

    Data only, no DDL, no deletes. The source row survives with whatever
    genuinely belongs to it. Skips any fixture whose move would collide with
    an existing (competition, home, away, kickoff) identity. Idempotent: a
    second run finds nothing left in that competition to move.
    """
    source_row = connection.execute(
        text("SELECT id FROM teams WHERE name = :name LIMIT 1"),
        {"name": source_name},
    ).fetchone()
    competition_row = connection.execute(
        text("SELECT id FROM competitions WHERE name = :name LIMIT 1"),
        {"name": competition_name},
    ).fetchone()
    if source_row is None or competition_row is None:
        return
    source_id = source_row[0]
    competition_id = competition_row[0]

    pending = connection.execute(
        text(
            "SELECT 1 FROM matches WHERE competition_id = :c"
            " AND (home_team_id = :s OR away_team_id = :s) LIMIT 1"
        ),
        {"c": competition_id, "s": source_id},
    ).fetchone()
    if pending is None:
        return  # nothing to move — already split, or never contaminated

    target_row = connection.execute(
        text("SELECT id FROM teams WHERE name = :name LIMIT 1"),
        {"name": target_name},
    ).fetchone()
    if target_row is None:
        target_id = str(uuid.uuid4())
        connection.execute(
            text(
                "INSERT INTO teams (id, name, country, is_placeholder)"
                " VALUES (:i, :n, :c, :p)"
            ),
            {
                "i": target_id,
                "n": target_name,
                "c": target_country,
                "p": _false_for_dialect(connection),
            },
        )
    else:
        target_id = target_row[0]
    if target_id == source_id:
        return

    for side, other in (("home_team_id", "away_team_id"), ("away_team_id", "home_team_id")):
        connection.execute(
            text(f"""
                UPDATE matches
                SET {side} = :target_id
                WHERE competition_id = :competition_id
                  AND {side} = :source_id
                  AND NOT EXISTS (
                      SELECT 1 FROM matches m2
                      WHERE m2.id != matches.id
                        AND m2.competition_id = matches.competition_id
                        AND m2.{side} = :target_id
                        AND m2.{other} = matches.{other}
                        AND m2.kickoff_at = matches.kickoff_at
                  )
            """),
            {
                "target_id": target_id,
                "source_id": source_id,
                "competition_id": competition_id,
            },
        )


def _false_for_dialect(connection):
    """SQLite stores booleans as integers; Postgres wants a real boolean."""
    return False if connection.engine.dialect.name != "sqlite" else 0


def _true_for_dialect(connection):
    """Counterpart of :func:`_false_for_dialect` for truthy comparisons."""
    return "TRUE" if connection.engine.dialect.name != "sqlite" else "1"


# The reverse direction of the same collision v23/v24 addressed: with
# "femenil" stripped, "Cruz Azul Femenil" resolved to the men's Cruz Azul, so
# most of Liga MX Femenil was ingested straight onto men's rows. 18 men's rows
# carry 289 women's fixtures between them, which corrupts the recent-form
# features of the league Progol is mostly made of.
#
# (men's row, women's row). Every pair is Liga MX Femenil — verified that no
# men's row holds fixtures from any other women's competition. The first seven
# targets already exist under the inconsistent names the sources produced, so
# they are mapped explicitly rather than derived: creating "Monterrey Femenil"
# next to "C.F. Monterrey Femenil" would duplicate the club, which is the mess
# v21 had to clean up. The remaining eleven have no women's row at all —
# the collision is why — and get one named after the men's club.
#
# "Washington Spirit" is deliberately absent: it holds one CONCACAF W fixture
# and no men's ones, because it is an NWSL club whose real name simply carries
# no gender marker. It was never contaminated.
_FEMENIL_ROW_SPLITS: tuple[tuple[str, str], ...] = (
    ("America", "CF América Femenil"),
    ("Guadalajara", "C.D. Guadalajara Femenil"),
    ("Monterrey", "C.F. Monterrey Femenil"),
    ("Pachuca", "C.F. Pachuca Femenil"),
    ("Pumas", "Club Universidad Nacional Femenil"),
    ("Toluca", "Deportivo Toluca F.C. Femenil"),
    ("Tigres", "Tigres UANL Femenil"),
    ("Atlas", "Atlas Femenil"),
    ("Atletico de San Luis", "Atletico de San Luis Femenil"),
    ("Cruz Azul", "Cruz Azul Femenil"),
    ("FC Juarez", "FC Juarez Femenil"),
    ("Leon", "Leon Femenil"),
    ("Mazatlán", "Mazatlán Femenil"),
    ("Necaxa", "Necaxa Femenil"),
    ("Puebla", "Puebla Femenil"),
    ("Queretaro FC", "Queretaro FC Femenil"),
    ("Santos Laguna", "Santos Laguna Femenil"),
    ("Tijuana", "Tijuana Femenil"),
)


# Liga MX Femenil rows carry the club names the *results* sources produced
# ("C.F. Pachuca Femenil", "Club Universidad Nacional Femenil"), while the
# fixture feed uses the short form ("Pachuca Femenil", "Pumas UNAM Femenil").
# find_team_by_alias needs either an exact name hit or an alias slug hit, and
# the rows v25 created were inserted as raw SQL with no alias at all — so the
# short forms resolved to nothing and the next Femenil ingest would have minted
# a second row per club. Verified against the live data before writing: seven
# of the fifteen names in the 2026-2027 fixture list resolved to nothing.
#
# (alias the feed emits, team row it belongs to). Only pairs where the two
# names genuinely differ; the other eleven clubs already match exactly.
_FEMENIL_TEAM_ALIASES: tuple[tuple[str, str], ...] = (
    ("América Femenil", "CF América Femenil"),
    ("Pachuca Femenil", "C.F. Pachuca Femenil"),
    ("Monterrey Femenil", "C.F. Monterrey Femenil"),
    ("Guadalajara Femenil", "C.D. Guadalajara Femenil"),
    ("Toluca Femenil", "Deportivo Toluca F.C. Femenil"),
    ("Pumas UNAM Femenil", "Club Universidad Nacional Femenil"),
    ("Juárez Femenil", "FC Juarez Femenil"),
    ("Querétaro Femenil", "Queretaro FC Femenil"),
    ("León Femenil", "Leon Femenil"),
    ("Atlético de San Luis Femenil", "Atletico de San Luis Femenil"),
)


# Placeholder rows the PG-2344 promotion minted because the Progol guide
# names clubs in short form and the short form resolved to nothing. Each
# placeholder holds exactly one fixture — the slate's — while the real row
# holds between 23 and 137, so the position was scored as if the club had no
# history: positions 12 and 13 came out BLOQUEADO with 86 and 85 matches
# sitting unused on the real rows.
#
# Every pair is verified against the fixture's competition, never by name
# similarity: "San Luis" is in a Liga MX fixture against Tijuana, "Barracas"
# in an Argentine one against Deportivo Riestra, "Salt Lake" and "Portland"
# in MLS ones, "Inter De Milán" in a Champions League tie against Manchester
# City. No competing club exists in the database for any of them.
#
# "Aberdeen" and "Hearts" are deliberately absent: both are placeholders with
# no real row behind them, because no registered source covers Scottish
# football. That is missing data, not a naming mismatch, and a merge would
# have nothing to merge into.
_GUIDE_PLACEHOLDER_MERGES: tuple[tuple[str, str], ...] = (
    ("San Luis", "Atletico de San Luis"),
    ("Inter De Milán", "FC Internazionale Milano"),
    ("Boca Jrs", "CA Boca Juniors"),
    ("Barracas", "Barracas Central"),
    ("Salt Lake", "Real Salt Lake"),
    ("Portland", "Portland Timbers"),
)


# Women's fixture documents that were linked to the men's row of the same
# club, back when the normalizer stripped "femenil" from team names. The
# gender_mismatch blocker added alongside v23-v26 stops new links, but the
# ones already written stayed put, and each one contributed an evidence
# item to a men's match's recent-form features.
_WOMENS_TITLE_MARKERS = ("femenil", "femenino", "femenina", "women")


# Two more club rows split across ingestion sources, both found on the
# live PG-2344 slate. (placeholder row, row that holds the history).
# Each pair was verified to share a competition, which is what the note
# above _CONTINENTAL_SPLIT_MERGES requires before any merge:
#
#   Manchester United (3, E0)  -> Man United (114, E0)
#   Seattle (3, MLS)           -> Seattle Sounders (87, MLS)
#
# Manchester City is deliberately NOT here. Its two rows sit in different
# competitions (Man City in E0, Manchester City FC in UEFA Champions
# League), which is the cross-competition case that note warns about, and
# its slate position is blocked by unclassified_competition anyway — the
# merge would not unblock it.
_SOURCE_SPLIT_MERGES = (
    ("Manchester United", "Man United"),
    ("Seattle", "Seattle Sounders"),
)


# Every remaining split the detector found, pre-emptively folded before a
# Progol guide happens to use the short form and blocks a position.
# (thin row, row holding the history). All seven were reported by
# team_row_split_detection, which only proposes a pair that shares a
# competition, so each one already satisfies the check the note above
# _CONTINENTAL_SPLIT_MERGES requires.
_DETECTED_ROW_SPLITS = (
    ("Vancouver", "Vancouver Whitecaps"),
    ("Minnesota", "Minnesota United"),
    ("San Jose", "San Jose Earthquakes"),
    ("New England", "New England Revolution"),
    ("Malm\u00f6", "Malmo FF"),
    ("Shimizu", "Shimizu S-Pulse"),
    ("Kalmar", "Kalmar FF"),
)


# (draw_code, position, competition the LN guide states) for fixtures whose
# competition was INFERRED by the promotion path rather than observed.
_GUIDE_COMPETITION_CORRECTIONS = (
    ("PGM-806", 2, "Liga de Expansion MX"),
    ("PGM-806", 5, "Copa Sudamericana"),
    ("PGM-806", 6, "Copa Sudamericana"),
    ("PGM-806", 8, "Argentinian Primera Division"),
    ("PGM-806", 9, "Argentinian Primera Division"),
)


def _migrate_to_v34(connection) -> None:
    """Correct five PGM-806 competitions against the official LN guide.

    When ``ProgolFixtureResolver`` finds no ingested fixture for a pair, it
    infers the competition from the teams' shared history — documented
    behaviour, and honest as far as it goes, but an inference. For PGM-806 the
    inference was wrong on five of nine positions, and the official
    ``guiamedia.pdf`` for concurso 806 states each one outright:

        pos 2  Oaxaca vs Sinaloa            "Jornada 2 de la Liga de Expansión"
        pos 5  Vasco da Gama vs I. Medellín "Playoffs de la Copa Sudamericana"
        pos 6  O'Higgins vs Boca Jrs        "Playoffs de la Copa Sudamericana"
        pos 8  Talleres vs Vélez            "Jornada 2 de la Liga Argentina"
        pos 9  Central Córdoba vs Tucumán   "Jornada 2 de la Liga Argentina"

    Positions 5 and 8 are the ones that matter most: they were not merely
    unresolved, they were confidently wrong. A Sudamericana playoff sat under
    "Brasileirao" and a league fixture under "Copa Libertadores", because that
    is where each pair had most often met before. Positions 2, 6 and 9 sat
    under the "Progol Concurso 806" placeholder.

    This does NOT touch ``composition_hash``. That hash is computed from the
    RAW PROMOTION PAYLOAD before entity resolution — see the contract on
    ``SlateRepository._compute_composition_hash`` — so it is a fingerprint of
    what was promoted, not of the competition rows the DB holds now. Existing
    predictions and ticket snapshots keep their linkage untouched.

    Positions 1, 3, 4 and 7 are deliberately left alone: 3, 4 and 7 already
    carry the competition the guide states, and 1 is the MLS/Liga MX All-Star
    exhibition, which belongs to no league and correctly stays under the
    concurso placeholder.

    Anchored by (draw_code, position) so it can only ever touch these five
    rows, with the same fixture-identity guard v15 uses. Idempotent: re-running
    finds the competition already correct and writes nothing. Mirrors
    alembic 0034.
    """
    for draw_code, position, competition_name in _GUIDE_COMPETITION_CORRECTIONS:
        target = connection.execute(
            text("SELECT id FROM competitions WHERE name = :name AND is_placeholder = false LIMIT 1"),
            {"name": competition_name},
        ).fetchone()
        if target is None:
            continue
        connection.execute(
            text("""
                UPDATE matches
                SET competition_id = :target_id
                WHERE id IN (
                    SELECT sm.match_id
                    FROM progol_slate_matches sm
                    JOIN progol_slates s ON s.id = sm.slate_id
                    WHERE s.draw_code = :draw_code AND sm.position = :position
                )
                AND competition_id != :target_id
                AND NOT EXISTS (
                    SELECT 1 FROM matches m2
                    WHERE m2.id != matches.id
                      AND m2.competition_id = :target_id
                      AND m2.home_team_id = matches.home_team_id
                      AND m2.away_team_id = matches.away_team_id
                      AND m2.kickoff_at = matches.kickoff_at
                )
            """),
            {"target_id": target[0], "draw_code": draw_code, "position": position},
        )


_DUPLICATE_FIXTURE_TOLERANCE_SECONDS = 48 * 3600

# (table, fk column, columns that must stay unique alongside the fk)
_MATCH_DEPENDENTS = (
    ("match_results", "match_id", ("source_id", "played_at")),
    ("match_live_results", "match_id", ("source_id",)),
    ("match_stat_snapshots", "match_id", ("source_id", "captured_at", "stat_type")),
    ("player_availability", "match_id",
     ("team_id", "player_name", "status", "category", "source_id", "captured_at")),
    ("evidence_items", "match_id", ()),
    ("match_feature_snapshots", "match_id", ()),
    ("predictions", "match_id", ()),
    ("source_documents", "matched_match_id", ()),
    # Must be here, and a dry run is what proved it: leaving the slate link
    # behind while the results move to the survivor strips the very coverage
    # this migration exists to restore. PGM-800 went from complete to 4/9 in a
    # rehearsal that omitted this line. `uq_progol_slate_match` keeps one link
    # per (slate, match), so a slate holding both rows of a cluster collapses
    # to a single link rather than duplicating a position.
    ("progol_slate_matches", "match_id", ("slate_id",)),
)


def _migrate_to_v37(connection) -> None:
    """Consolidate fixtures that exist twice because feeds disagree on kickoff.

    ``uq_matches_fixture_identity`` keys on the exact kickoff, so one real
    match reported by two sources an hour apart is two rows, and everything
    that arrives later splits between them. Production carries 41 such groups:
    14 have a result on both sides, and 27 have a hollow twin.

    The hollow twin is the expensive half. PGM-797 has five positions pointing
    at rows that hold the EVIDENCE while their twins hold the RESULTS, which is
    why that slate reports 1/9 canonical coverage while eight results sit in
    the database. Note the direction — the row the slate points at is not the
    empty one, so relinking the slate to the twin would trade one loss for
    another. Consolidation has to move the dependents, not the link.

    Survivor per cluster, in order: the row a slate already points at, then a
    real row over a fabricated one, then the row carrying more results, then
    the earliest kickoff. Keeping the slate's row means predictions, snapshots
    and ticket history stay attached to what produced them.

    Every dependent is re-pointed, skipping rows that would collide with a
    unique key the survivor already satisfies. Losers are then marked
    ``is_placeholder`` so no resolver returns them again. Nothing is deleted:
    a skipped dependent stays readable on a row that is out of circulation.

    The companion change is in ``SlateRepository.upsert_slate``, which now
    falls back to ``find_match_near_identity`` before creating a row, so
    promotion stops minting these. This migration only cleans up what the
    old behaviour already produced. Mirrors alembic 0037.
    """
    pair_sql = (
        """
        SELECT a.id, b.id
        FROM matches a
        JOIN matches b
          ON a.competition_id = b.competition_id
         AND a.home_team_id  = b.home_team_id
         AND a.away_team_id  = b.away_team_id
         AND a.id < b.id
        WHERE ABS(EXTRACT(EPOCH FROM (a.kickoff_at - b.kickoff_at))) <= :tolerance
        """
        if connection.dialect.name != "sqlite"
        else """
        SELECT a.id, b.id
        FROM matches a
        JOIN matches b
          ON a.competition_id = b.competition_id
         AND a.home_team_id  = b.home_team_id
         AND a.away_team_id  = b.away_team_id
         AND a.id < b.id
        WHERE ABS((julianday(a.kickoff_at) - julianday(b.kickoff_at)) * 86400.0)
              <= :tolerance
        """
    )
    pairs = connection.execute(
        text(pair_sql), {"tolerance": _DUPLICATE_FIXTURE_TOLERANCE_SECONDS}
    ).fetchall()
    if not pairs:
        return

    # Union the pairs into clusters so a fixture recorded three times folds
    # into one survivor rather than into two half-merges.
    parent: dict[str, str] = {}

    def _find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def _union(left: str, right: str) -> None:
        left_root, right_root = _find(left), _find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in pairs:
        _union(left, right)

    clusters: dict[str, list[str]] = {}
    for node in list(parent):
        clusters.setdefault(_find(node), []).append(node)

    for members in clusters.values():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda mid: _duplicate_survivor_rank(connection, mid))
        survivor, losers = ranked[0], ranked[1:]
        for loser in losers:
            _repoint_match_dependents(connection, loser, survivor)
            connection.execute(
                text("UPDATE matches SET is_placeholder = :yes WHERE id = :id"),
                {"yes": True, "id": loser},
            )


def _duplicate_survivor_rank(connection, match_id: str) -> tuple:
    """Lower sorts first: slate link, then real over fabricated, then more
    results, then earliest kickoff, then id so the order is deterministic."""
    row = connection.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM progol_slate_matches sm WHERE sm.match_id = m.id),
              m.is_placeholder,
              (SELECT COUNT(*) FROM match_results r WHERE r.match_id = m.id),
              m.kickoff_at
            FROM matches m WHERE m.id = :id
            """
        ),
        {"id": match_id},
    ).fetchone()
    if row is None:
        return (1, 1, 0, "", match_id)
    slate_links, is_placeholder, results, kickoff = row
    return (
        0 if slate_links else 1,
        1 if is_placeholder else 0,
        -int(results or 0),
        str(kickoff),
        match_id,
    )


def _repoint_match_dependents(connection, loser_id: str, survivor_id: str) -> None:
    for table, column, unique_rest in _MATCH_DEPENDENTS:
        if not _table_exists(connection, table):
            continue
        if unique_rest:
            conflict = " AND ".join(f"other.{col} = {table}.{col}" for col in unique_rest)
            statement = text(
                f"""
                UPDATE {table}
                SET {column} = :survivor
                WHERE {column} = :loser
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS other
                      WHERE other.{column} = :survivor AND {conflict}
                  )
                """
            )
        else:
            statement = text(
                f"UPDATE {table} SET {column} = :survivor WHERE {column} = :loser"
            )
        connection.execute(statement, {"survivor": survivor_id, "loser": loser_id})


_BRASILEIRAO_PROVIDER_SPLITS = (
    # (duplicate to retire, canonical to keep)
    ("Cruzeiro EC", "Cruzeiro"),
    ("Flamengo", "CR Flamengo"),
    ("Coritiba FBC", "Coritiba"),
    ("Grêmio FBPA", "Gremio"),
    ("Bahia", "EC Bahia"),
    ("SC Corinthians Paulista", "Corinthians"),
    ("Palmeiras", "SE Palmeiras"),
    ("Fortaleza EC", "Fortaleza"),
    ("Chapecoense AF", "Chapecoense"),
    ("EC Juventude", "Juventude"),
    ("Clube do Remo", "Remo"),
    ("CR Vasco da Gama", "Vasco da Gama"),
    ("SC Recife", "Sport Club do Recife"),
    ("Atlético Mineiro", "CA Mineiro"),
    ("CA Paranaense", "Athletico Paranaense"),
)


def _fold_provider_split(connection, duplicate_name: str, canonical_name: str) -> None:
    """Merge a provider-split club row and close the door behind it.

    ``_merge_team_into`` alone is not durable here. It re-points matches and
    moves aliases but leaves the duplicate row under its own name, and
    ``EntityRepository.find_team_by_alias`` matches on ``teams.name`` as well
    as on the alias table — so the next ingest from the feed that writes the
    duplicate spelling resolves straight back to it and the split reopens on
    the following jornada.

    So after the merge this pins the duplicate's spelling as an alias of the
    canonical row and retires the duplicate: renamed ``<name> (merged)`` and
    flagged a placeholder, which is what ``merge_duplicate_team.py`` does and
    what the ``is_placeholder ASC`` ordering in that resolver relies on.

    Nothing is deleted. Fixtures that would collide with one already sitting
    on the canonical row stay behind on the retired row — they are duplicates
    of a fixture the canonical already carries, and a retired row is never
    resolved again, so they fall out of every form window.
    """
    from app.services.normalization_service import NormalizationService

    duplicate = connection.execute(
        text("SELECT id FROM teams WHERE name = :n LIMIT 1"), {"n": duplicate_name}
    ).fetchone()
    canonical = connection.execute(
        text("SELECT id FROM teams WHERE name = :n LIMIT 1"), {"n": canonical_name}
    ).fetchone()
    if duplicate is None or canonical is None or duplicate[0] == canonical[0]:
        return  # fresh DB, already folded, or same row

    _merge_team_into(connection, duplicate_name, canonical_name)

    # Pin the retired spelling so the feed that writes it resolves to the
    # survivor. Both alias columns are unique; skip when anything already
    # owns the string rather than stealing it from another team.
    slug = NormalizationService().normalize_team_name(duplicate_name)
    taken = connection.execute(
        text(
            "SELECT team_id FROM team_aliases"
            " WHERE alias = :a OR normalized_alias = :s LIMIT 1"
        ),
        {"a": duplicate_name, "s": slug},
    ).fetchone()
    if taken is None:
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES (:i, :t, :a, :s)"
            ),
            {"i": str(uuid.uuid4()), "t": canonical[0], "a": duplicate_name, "s": slug},
        )

    connection.execute(
        text(
            "UPDATE teams SET name = :merged, is_placeholder = TRUE"
            " WHERE id = :id AND name = :original"
        ),
        {"merged": f"{duplicate_name} (merged)", "id": duplicate[0], "original": duplicate_name},
    )


def _migrate_to_v36(connection) -> None:
    """Fold the Brasileirão club rows that two feeds split in half.

    football-data.org and TheSportsDB spell Brazilian clubs differently, and
    neither spelling was pinned as an alias of the other, so most of the
    league is carrying two parallel rows: one per feed, each holding half the
    history. Fifteen clubs are affected.

    Found through PGM-808 position 9, Cruzeiro vs Flamengo. The slate resolved
    to ``Cruzeiro`` (116 matches, last played 2026-07-30) while ``Cruzeiro EC``
    (66 matches, last played 2026-08-09) held the fixtures the results feed
    keeps writing. Flamengo is split the same way but ASYMMETRICALLY — the
    slate landed on ``CR Flamengo``, the rich side. So the model compared a
    fully-fed team against one missing two rounds, read the gap as 9.7 days of
    extra rest, and flipped the pick from 1 to 2 while promoting the position
    from REVISAR to LISTO. The pick was an artefact of the split, not a read
    on the match.

    Membership is established by fixture identity, never by name similarity.
    Two rows for one club cannot hold different opponents at the same kickoff;
    two rows for different clubs cannot hold the same opponent at the same
    kickoff over and over. Every pair below has zero conflicts and positive
    overlap once opponents are themselves resolved through this same mapping —
    that second step matters, because the raw comparison reports Cruzeiro's 13
    "conflicts" as different opponents when they are Chapecoense vs
    Chapecoense AF, Gremio vs Grêmio FBPA and so on: the same match, with the
    rival written under its own split pair.

    Direction follows the row carrying more results, which is the guard
    ``merge_duplicate_team.py`` enforces. It is safe to pick on that basis
    rather than on which feed is live, because the retired spelling is pinned
    as an alias, so either feed resolves to the survivor afterwards.

    Deliberately excluded:

    * ``Botafogo`` / ``Botafogo-SP`` — different clubs (Rio and Ribeirão
      Preto). Two same-kickoff fixtures against different opponents prove it,
      and this is exactly the false positive a name-similarity sweep makes.
    * ``América Mineiro`` / ``Athletic Club-MG`` — different clubs, no shared
      fixture anywhere.
    * ``Atletico MG`` / ``Atletico PR`` / ``Atletico GO`` — a 2024-only CSV
      season (38 matches each, all 2024-04-14 to 2024-12-08) that overlaps no
      modern row, so nothing available proves identity. They are almost
      certainly Mineiro, Paranaense and Goianiense, but "almost certainly" is
      not the standard the rest of this list meets.
    * ``Inter Porto Alegre`` — a one-match placeholder with no result and no
      overlap with ``Internacional``. Placeholders already lose resolution
      priority, so it costs nothing to leave it.

    Same contract as v14/v16/v21/v27/v30/v31/v33/v35: additive, nothing
    deleted, no-op when a row is absent, idempotent on re-run. Does not touch
    composition_hash, which fingerprints the promotion payload rather than the
    DB rows. Mirrors alembic 0036.
    """
    for duplicate_name, canonical_name in _BRASILEIRAO_PROVIDER_SPLITS:
        _fold_provider_split(connection, duplicate_name, canonical_name)


def _migrate_to_v35(connection) -> None:
    """Fold the Manchester City split that v30 deferred.

    v30 looked at this pair and deliberately left it alone, for two stated
    reasons. Both have since stopped holding:

    * *"its rows are split ACROSS competitions"* — Man City (114, E0) and
      Manchester City FC (21, UEFA Champions League) share no competition, so
      the shared-competition guard that protects against name-similarity
      false positives ("Real Sociedad" -> "Real Madrid") had nothing to check.
      The evidence here is not name similarity: the UCL row's opponents are
      Real Madrid, Inter, PSG, Juventus, Dortmund and Leverkusen, which is the
      senior men's Manchester City and no one else. The LN guide for concurso
      2344 writes the same club both ways — "MANCHESTER CITY" in casillero 5,
      "Man. City" in its own context line.
    * *"blocked by unclassified_competition rather than by thin history, so
      the merge would not have unblocked it"* — true until today's published
      walk-forward gave the Champions League an audited benchmark, which
      moved it off `unclassified`. With that gone, thin history is exactly
      what is left holding position 5 down: the slate points at the 21-match
      row while 114 matches, four of them inside the active form window, sit
      on the other.

    Direction follows the richer row and the league feed that keeps writing
    it: FD-UK's E0 emits "Man City" every season. The normalization pins added
    alongside this migration make the UCL feed resolve there too, so the split
    cannot reopen on the next ingest.

    Same contract as v14/v16/v21/v27/v30/v31/v33: additive, nothing deleted,
    no-op when a row is absent. Does not touch composition_hash, which
    fingerprints the promotion payload rather than the DB rows. Mirrors
    alembic 0035.
    """
    _merge_team_into(connection, "Manchester City FC", "Man City")


_ARGENTINE_ROW_SPLITS = (
    ("Argentinos Jrs", "Argentinos Juniors"),
    ("Instituto ACC", "Instituto"),
    ("Gimnasia LP", "Gimnasia y Esgrima de La Plata"),
)


def _migrate_to_v33(connection) -> None:
    """Fold the three split rows in the Argentine Primera before the
    football-data.co.uk feed lands on top of them.

    The TheSportsDB free tier caps ``eventsday.php`` at three events per
    call, so the Argentine league arrives with holes — Barracas Central's
    2026-07-25 fixture never came through, which is one of the two reasons
    PG-2344 position 10 is blocked. The repair is to ingest ARG.csv, which
    has no cap. But that CSV writes club names in short form, and three of
    them currently land on the wrong row:

        Argentinos Jrs (1, placeholder)  -> Argentinos Juniors (21)
        Instituto ACC (13, last 2024)    -> Instituto (8, last 2026)
        Gimnasia LP (10, last 2024)      -> Gimnasia y Esgrima de La Plata (7, last 2026)

    "Argentinos Jrs" is the live trap: the CSV writes exactly that string,
    so ingesting first would have piled a season onto a one-match
    placeholder while the club's real history sat beside it. The other two
    are temporal splits — one source named the club until 2024, another
    names it now — so each side holds half the history.

    Direction is chosen by which name the LIVE feeds write, not by which
    row is currently richer: the surviving row has to be the one the next
    ingest will resolve to, or the split simply reopens. That is why
    Instituto ACC (13 matches) folds into Instituto (8) and not the
    reverse.

    All three pairs sit inside "Argentinian Primera Division", which is the
    shared-competition check the note above ``_CONTINENTAL_SPLIT_MERGES``
    requires before any merge. Same contract as v14/v16/v21/v27/v30/v31:
    additive, nothing deleted, no-op when a row is absent. Mirrors
    alembic 0033.

    Deliberately NOT included, for the reasons v30 already recorded:
    Manchester City FC (21, UCL) / Man City (114, E0) share no competition,
    and position 5 is blocked by unclassified_competition rather than by
    thin history, so folding them would repair nothing.
    """
    for split_name, canonical_name in _ARGENTINE_ROW_SPLITS:
        _merge_team_into(connection, split_name, canonical_name)


def _migrate_to_v32(connection) -> None:
    """Mark the fixtures the Progol promotion path fabricated.

    When ``ProgolFixtureResolver`` finds no ingested match for a pair the LN
    guide lists, promotion still has to produce 9 or 14 positions, so it builds
    one: kickoff at ``cierre + 12h``, stepped an hour per position, with a
    competition inferred from team history. Nothing recorded that the row was a
    construction, so it sat in ``matches`` looking exactly like a fixture a feed
    had reported. Three consequences, all live in production before this
    revision:

    * the pick card printed the invented hour as the fixture's kickoff;
    * ``find_upcoming_match_for_pair`` returned a previous slate's fabricated
      row as the "real match" for a new slate, so 16 match rows ended up shared
      between two slates (PG-2336/PGM-799, PG-2337/PGM-800, PG-2334/PGR-2334);
    * correcting a cierre never moved the kickoffs derived from the old one —
      PGM-806 closed 2026-07-28 22:55Z with all 9 kickoffs on 2026-07-30.

    The backfill recognises fabricated rows by their shape, not by recomputing
    ``cierre + 12h``: once an operator corrects the cierre the stored kickoffs
    no longer relate to it, and those rows are precisely the ones that most need
    marking. Within a slate of >= 3 positions, kickoffs spaced at exactly one
    hour that collapse to a single base are the fabricator's signature — 10 of
    the 16 slates in production match it, including the active PG-2344, and each
    of the 9 whose cierre was never corrected also reproduces ``cierre + 12h``
    exactly, which is what confirms the detection rather than defines it.

    Additive and conservative: a row only gains the mark, defaults are False,
    and a slate that resolved to real fixtures forms no ladder and is untouched.
    Mirrors alembic 0032. See app.services.placeholder_fixtures.
    """
    _add_column_if_missing(
        connection,
        "matches",
        "is_placeholder",
        f"is_placeholder BOOLEAN NOT NULL DEFAULT {_false_for_dialect(connection)}",
    )
    # One base per slate: kickoff shifted back by (position - 1) hours. A
    # fabricated ladder collapses to exactly one distinct base; anything with
    # real kickoffs in it does not.
    ladders = connection.execute(
        text(
            """
            SELECT sm.slate_id
            FROM progol_slate_matches sm
            JOIN matches m ON m.id = sm.match_id
            GROUP BY sm.slate_id
            HAVING COUNT(*) >= 3
               AND COUNT(DISTINCT m.kickoff_at - (sm.position - 1) * INTERVAL '1 hour') = 1
            """
        )
        if connection.dialect.name != "sqlite"
        else text(
            """
            SELECT sm.slate_id
            FROM progol_slate_matches sm
            JOIN matches m ON m.id = sm.match_id
            GROUP BY sm.slate_id
            HAVING COUNT(*) >= 3
               AND COUNT(DISTINCT datetime(m.kickoff_at, '-' || (sm.position - 1) || ' hours')) = 1
            """
        )
    ).scalars().all()
    for slate_id in ladders:
        connection.execute(
            text(
                "UPDATE matches SET is_placeholder = "
                f"{_true_for_dialect(connection)} WHERE id IN ("
                "SELECT match_id FROM progol_slate_matches WHERE slate_id = :slate_id)"
            ),
            {"slate_id": slate_id},
        )


def _migrate_to_v31(connection) -> None:
    """Fold the seven remaining split club rows before they cost a slate.

    v30 repaired two splits after they had already blocked two PG-2344
    positions. These seven are the same defect caught before it lands:
    each is a thin row (0-10 matches) sitting beside the row that holds
    the club's real history in the same competition.

        Vancouver (2)    -> Vancouver Whitecaps (91)      MLS
        Minnesota (3)    -> Minnesota United (88)         MLS
        San Jose (2)     -> San Jose Earthquakes (82)     MLS
        New England (2)  -> New England Revolution (82)   MLS
        Malmo (10)       -> Malmo FF (72)                 Allsvenskan
        Shimizu (3)      -> Shimizu S-Pulse (58)          J1 League
        Kalmar (10)      -> Kalmar FF (39)                Allsvenskan

    Same contract as v14/v16/v21/v27/v30. Mirrors alembic 0031.
    """
    for placeholder_name, canonical_name in _DETECTED_ROW_SPLITS:
        _merge_team_into(connection, placeholder_name, canonical_name)


def _migrate_to_v30(connection) -> None:
    """Consolidate two club rows that two ingestion sources split.

    PG-2344 came out with 5 of 14 positions blocked. Two of them —
    "Man United vs Atletico de Madrid" and "Portland Timbers vs Seattle"
    — were blocked on insufficient_data_anchors purely because the slate
    resolved to the 3-match placeholder row instead of the one carrying
    the club's real history. 111 and 84 matches respectively were sitting
    unused on the other row.

    Same contract as v14/v16/v21/v27: _merge_team_into re-points matches
    and aliases, skips anything that would collide with an existing
    fixture identity, never deletes the source row, and is a no-op when
    either side is absent. Mirrors alembic 0030.
    """
    for placeholder_name, canonical_name in _SOURCE_SPLIT_MERGES:
        _merge_team_into(connection, placeholder_name, canonical_name)


def _migrate_to_v29(connection) -> None:
    """Unlink women's documents from men's matches and drop the evidence
    they produced.

    Match results are NOT touched and were never wrong: every result on
    the affected fixtures came from the men's own source (TSDB Liga MX),
    so the scores are genuine. The damage was confined to evidence — 25
    documents linked onto 21 men's Liga MX matches, each adding a
    women's fixture to the men's form window.

    The documents are only unlinked, never deleted: they are real
    documents about real matches, and the next ingestion run can link
    them correctly now that gender_mismatch blocks the bad pairing.
    Idempotent — re-running finds nothing left to unlink. Mirrors
    alembic 0029.
    """
    marker_clause = " OR ".join(
        f"LOWER(d.title) LIKE '%{marker}%'" for marker in _WOMENS_TITLE_MARKERS
    )
    team_clause = " AND ".join(
        f"LOWER(ht.name) NOT LIKE '%{marker}%' AND LOWER(at.name) NOT LIKE '%{marker}%'"
        for marker in _WOMENS_TITLE_MARKERS
    )
    select_bad = f"""
        SELECT d.id AS document_id, d.linked_evidence_id AS evidence_id
        FROM source_documents d
        JOIN matches m ON m.id = d.matched_match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE ({marker_clause}) AND ({team_clause})
    """
    rows = connection.execute(text(select_bad)).fetchall()
    if not rows:
        return

    document_ids = [row[0] for row in rows]
    evidence_ids = [row[1] for row in rows if row[1]]

    for document_id in document_ids:
        connection.execute(
            text(
                "UPDATE source_documents "
                "SET matched_match_id = NULL, linked_evidence_id = NULL "
                "WHERE id = :id"
            ),
            {"id": document_id},
        )
    for evidence_id in evidence_ids:
        connection.execute(
            text("DELETE FROM evidence_items WHERE id = :id"), {"id": evidence_id}
        )


def _migrate_to_v28(connection) -> None:
    """Persist the competition stage/round a fixture belongs to.

    TheSportsDB already returns ``strRound`` on every event and we threw
    it away. Without it nothing can tell a Champions League group match
    from a semi-final, or a Liga MX week-3 fixture from a liguilla
    final, because both sides of each pair carry the same competition
    name. That gap is why the knockout flag stayed a manual chore and
    why the knockout draw-rate anchor could not be refit from our own
    results.

    Nullable and additive: every existing row keeps a NULL stage and
    every consumer treats NULL as "unknown", so nothing changes until
    the connector starts populating it. Mirrors alembic 0028.
    """
    _add_column_if_missing(
        connection,
        "matches",
        "stage",
        "stage VARCHAR(64)",
    )
    # Who decided is_knockout. Stage data usually arrives after the
    # slate is built, so something has to be allowed to revise the flag
    # later — but it must never overwrite a human. 'operator' rows are
    # frozen; 'auto' rows stay open to better evidence. Existing rows
    # default to 'auto', which is accurate: before this column the only
    # two flagged positions in production were set by hand, and they
    # are re-marked as operator rows by the backfill below.
    _add_column_if_missing(
        connection,
        "progol_slate_matches",
        "knockout_source",
        "knockout_source VARCHAR(16) NOT NULL DEFAULT 'auto'",
    )
    connection.execute(
        text(
            "UPDATE progol_slate_matches SET knockout_source = 'operator' "
            f"WHERE is_knockout = {_true_for_dialect(connection)}"
        )
    )


def _migrate_to_v27(connection) -> None:
    """Fold the PG-2344 guide placeholders into the clubs they name.

    Same contract as v14/v16/v21: ``_merge_team_into`` re-points matches and
    aliases, skips anything that would collide with an existing fixture
    identity, never deletes the source row, and is a no-op when either side
    is absent. Mirrors alembic 0027.
    """
    for placeholder_name, canonical_name in _GUIDE_PLACEHOLDER_MERGES:
        _merge_team_into(connection, placeholder_name, canonical_name)


def _migrate_to_v26(connection) -> None:
    """Give the Liga MX Femenil rows the aliases the fixture feed needs.

    Also backfills a self-alias for every women's row that has none, so a
    club is reachable by its own normalized slug and not only by an exact
    name match — that gap is what made accents and "FC"/"C.F." prefixes
    decide whether a lookup succeeded.

    Data only, no DDL, no deletes. Idempotent, and skips any alias another
    team already owns rather than moving it. Mirrors alembic 0026.
    """
    from app.services.normalization_service import NormalizationService

    normalizer = NormalizationService()

    def _attach(alias: str, team_name: str) -> None:
        team = connection.execute(
            text("SELECT id FROM teams WHERE name = :n LIMIT 1"), {"n": team_name}
        ).fetchone()
        if team is None:
            return
        slug = normalizer.normalize_team_name(alias)
        # Both columns are unique: `ix_team_aliases_alias` on the raw text and
        # `uq_team_alias_normalized` on the slug. The slug check is the one
        # that matters here — "América Femenil" and "CF América Femenil"
        # normalize to the same `america-femenil`, so attaching the short form
        # makes the self-alias for the full name redundant, not missing.
        taken = connection.execute(
            text(
                "SELECT team_id FROM team_aliases"
                " WHERE alias = :a OR normalized_alias = :s LIMIT 1"
            ),
            {"a": alias, "s": slug},
        ).fetchone()
        if taken is not None:
            return  # already reachable, here or elsewhere — never steal it
        connection.execute(
            text(
                "INSERT INTO team_aliases (id, team_id, alias, normalized_alias)"
                " VALUES (:i, :t, :a, :s)"
            ),
            {"i": str(uuid.uuid4()), "t": team[0], "a": alias, "s": slug},
        )

    for alias, team_name in _FEMENIL_TEAM_ALIASES:
        _attach(alias, team_name)

    womens_rows = connection.execute(
        text(
            "SELECT name FROM teams"
            " WHERE LOWER(name) LIKE '%femenil%' OR LOWER(name) LIKE '%femenino%'"
        )
    ).fetchall()
    for (name,) in womens_rows:
        _attach(name, name)


def _migrate_to_v25(connection) -> None:
    """Move Liga MX Femenil fixtures off the men's rows that absorbed them.

    Same helper and same contract as v24 — collision-safe, no deletes, a
    no-op on a database that never had the contamination. Dry-run on the
    live data before writing this: 68 fixtures move onto the seven existing
    women's rows with zero fixture-identity collisions, the rest onto rows
    this creates. Mirrors alembic 0025.
    """
    for mens_name, womens_name in _FEMENIL_ROW_SPLITS:
        _split_team_matches_by_competition(
            connection, mens_name, "Liga MX Femenil", womens_name, "Mexico"
        )


def _migrate_to_v24(connection) -> None:
    """Split the club rows that the femenil normalization collision merged.

    v23 stopped new contamination; this repairs what the old rule already
    fused. See ``_MERGED_TEAM_SPLITS`` for the per-competition mapping and
    why each one is unambiguous. Mirrors alembic 0024.
    """
    for source, competition, target, country in _MERGED_TEAM_SPLITS:
        _split_team_matches_by_competition(connection, source, competition, target, country)


def _migrate_to_v23(connection) -> None:
    """Recompute `team_aliases.normalized_alias` for women's sides.

    Until v23 the team normalizer treated "femenil"/"femenino" as
    stopwords, so every women's alias was stored with the marker stripped:
    "Tigres UANL Femenil" landed as `tigres-uanl` and "Cruz Azul Femenil"
    as plain `cruz-azul`. Two things followed. A women's fixture resolved
    to the men's club of the same name, and — the direction that is easy
    to miss — a men's name could resolve to a women's row, because the
    stripped alias sits in the same lookup namespace.

    Dropping the stopword fixes new lookups but leaves the stored aliases
    on the old slug, so this re-derives them with the same rule the
    service now applies: strip accents and punctuation, drop the
    remaining stopwords, join with "-".

    Data only, no DDL, no deletes. Idempotent — a second run recomputes
    the same values. Skips any row whose new slug another team already
    holds: ``uq_team_alias_normalized`` makes that a hard constraint, and
    handing one slug to two teams would leave ``find_team_by_alias`` to
    resolve it arbitrarily anyway. Mirrors alembic 0023.
    """
    rows = connection.execute(
        text(
            "SELECT id, team_id, alias FROM team_aliases "
            "WHERE LOWER(alias) LIKE '%femenil%' OR LOWER(alias) LIKE '%femenino%'"
        )
    ).fetchall()
    if not rows:
        return
    from app.services.normalization_service import NormalizationService

    normalizer = NormalizationService()
    for alias_id, team_id, alias in rows:
        new_slug = normalizer.normalize_team_name(alias)
        if not new_slug:
            continue
        clash = connection.execute(
            text(
                "SELECT team_id FROM team_aliases "
                "WHERE normalized_alias = :slug AND id <> :id"
            ),
            {"slug": new_slug, "id": alias_id},
        ).fetchone()
        if clash is not None and clash[0] != team_id:
            continue
        connection.execute(
            text("UPDATE team_aliases SET normalized_alias = :slug WHERE id = :id"),
            {"slug": new_slug, "id": alias_id},
        )


def _migrate_to_v22(connection) -> None:
    """Merge the domestic/continental split rows for River Plate and Atlético
    Nacional (Colombia).

    Same contract as v21: data only, no deletes, collision-safe, idempotent,
    and a no-op when the rows are absent. Mirrors alembic 0022.
    """
    for source_name, canonical_name in _CONTINENTAL_SPLIT_MERGES:
        _merge_team_into(connection, source_name, canonical_name)


def _migrate_to_v21(connection) -> None:
    """Merge club rows that ingestion sources split under different names.

    Data-only: no DDL, no deletes. Each pair re-points match and alias
    references onto the canonical row via ``_merge_team_into``, which already
    skips anything that would collide with an existing fixture identity.

    Idempotent — a second run finds nothing left to move — and a no-op for any
    pair whose rows do not exist (fresh DB / tests). Mirrors alembic 0021.
    """
    for source_name, canonical_name in _DUPLICATE_CLUB_MERGES:
        _merge_team_into(connection, source_name, canonical_name)


def _migrate_to_v20(connection) -> None:
    """Drop team_rating_runs / team_rating_snapshots (subsystem removed).

    The team-rating subsystem (ratings, gate, shadow and canary) was removed
    in full, so these two tables have no owning code left. Dropping them keeps
    the runtime schema equal to ``Base.metadata`` instead of leaving orphan
    tables behind.

    Snapshots are dropped first because they carry the FK to the runs table.
    Idempotent via DROP TABLE IF EXISTS; a no-op on databases that never
    reached v19. Mirrors the alembic revision 0020.
    """
    connection.execute(text("DROP TABLE IF EXISTS team_rating_snapshots"))
    connection.execute(text("DROP TABLE IF EXISTS team_rating_runs"))
