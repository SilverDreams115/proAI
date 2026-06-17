from contextlib import contextmanager
import fcntl
from pathlib import Path
import re

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.base import Base

SCHEMA_VERSION = 18
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
    Liguilla final, etc.) must produce a winner — the boleta in those
    positions does not accept "X". The slate-match level (not the
    match level) is the right scope: the same fixture pair could be a
    friendly one week and a final the next.
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
        _merge_national_team_placeholder(connection, placeholder_name, canonical_name)


def _merge_national_team_placeholder(connection, placeholder_name: str, canonical_name: str) -> None:
    """Move all match references from the placeholder team to the canonical team.

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
    _merge_national_team_placeholder(connection, "Re P. Corea", "South Korea")


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
