from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
import uuid

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.base import Base

SCHEMA_VERSION = 27
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
