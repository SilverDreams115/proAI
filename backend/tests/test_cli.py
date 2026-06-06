"""Smoke + happy-path coverage for the operator CLI (S4.1).

The CLI is the operator-facing surface: `proai production-check`,
`proai prune-source-documents`, `proai publish-backtest`. Until now
it had zero test coverage, which meant an argparse rename or a
silently-failed migration could break an on-call command without
anyone noticing. These tests don't assert business logic depth
— that's covered elsewhere — they just lock in the entrypoint
shape and the JSON output contract operators script against.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone


def _isolated_db(tmp_path):
    """Point the global session at a fresh sqlite file and apply
    the schema, the way every other test does. We can't use the
    `client` fixture here because the CLI talks to the global
    session directly via `db_session.SessionLocal`."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import session as db_session
    from app.db.base import Base
    from app.db.migrations import run_migrations
    from app.models import tables  # noqa: F401 - ensure ORM models are registered

    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    db_session.engine = engine
    db_session.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return db_session


def _run_cli(args_list) -> dict:
    """Invoke the CLI parser with a synthetic argv and capture the
    JSON stdout. Centralises stdout teardown so individual tests
    only have to reason about the parsed payload."""
    from app.cli import build_parser

    parser = build_parser()
    ns = parser.parse_args(args_list)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ns.func(ns)
    return json.loads(buf.getvalue())


def test_build_parser_exposes_every_subcommand() -> None:
    """If an operator-facing command is renamed or dropped, the
    runbook breaks silently. This guards the surface area."""
    from app.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        action for action in parser._actions  # type: ignore[attr-defined]
        if action.__class__.__name__ == "_SubParsersAction"
    )
    expected = {
        "refresh-current",
        "ensure-current-job",
        "evaluate",
        "publish-backtest",
        "production-check",
        "prune-source-documents",
        "evaluate-xg",
    }
    assert set(subparsers_action.choices.keys()) == expected


def test_production_check_returns_expected_shape(tmp_path, monkeypatch) -> None:
    """The /production-check JSON is consumed by Makefile + ops
    scripts. We lock the keys + ready boolean shape so a refactor
    of the inner audit functions can't break the contract without
    a test reminding us to update the runbook too."""
    _isolated_db(tmp_path)
    payload = _run_cli(["production-check"])
    assert set(payload.keys()) == {"environment", "database_url", "ready", "errors"}
    assert isinstance(payload["ready"], bool)
    assert isinstance(payload["errors"], list)


def test_prune_source_documents_dry_run_reports_count(tmp_path, monkeypatch) -> None:
    """Dry-run must not delete anything and must surface the
    candidate count so operators can verify expected fan-out
    before scheduling the prune via cron."""
    from sqlalchemy import select, func

    from app.models.tables import (
        IngestionRunModel,
        SourceDocumentModel,
        SourceModel,
    )

    db_session = _isolated_db(tmp_path)
    s = db_session.SessionLocal()
    try:
        source = SourceModel(
            name="Stale Source",
            base_url="https://example.com",
            kind="json_feed",
            parser_profile="generic",
            is_active=True,
        )
        s.add(source)
        s.flush()
        run = IngestionRunModel(
            source_id=source.id,
            status="completed",
            documents_found=2,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        s.add(run)
        s.flush()
        stale_at = datetime.now(timezone.utc) - timedelta(days=120)
        # One stale + one fresh; the dry-run should report exactly one
        # candidate (the stale orphan).
        s.add(
            SourceDocumentModel(
                source_id=source.id,
                ingestion_run_id=run.id,
                external_url="https://example.com/old",
                title="old",
                summary="",
                payload_json="{}",
                normalized_key="old",
                captured_at=stale_at,
            )
        )
        s.add(
            SourceDocumentModel(
                source_id=source.id,
                ingestion_run_id=run.id,
                external_url="https://example.com/fresh",
                title="fresh",
                summary="",
                payload_json="{}",
                normalized_key="fresh",
                captured_at=datetime.now(timezone.utc),
            )
        )
        s.commit()
    finally:
        s.close()

    payload = _run_cli(["prune-source-documents", "--older-than-days", "90", "--dry-run"])
    assert payload["dry_run"] is True
    assert payload["would_delete"] == 1

    # Verify nothing was actually deleted.
    verify = db_session.SessionLocal()
    try:
        count = verify.scalar(select(func.count()).select_from(SourceDocumentModel))
        assert count == 2
    finally:
        verify.close()


def test_prune_source_documents_actually_deletes(tmp_path) -> None:
    """Same fixture as dry-run, but with --dry-run omitted: the
    stale orphan must be gone and the deleted counter must match."""
    from sqlalchemy import select, func

    from app.models.tables import (
        IngestionRunModel,
        SourceDocumentModel,
        SourceModel,
    )

    db_session = _isolated_db(tmp_path)
    s = db_session.SessionLocal()
    try:
        source = SourceModel(
            name="Stale Source",
            base_url="https://example.com",
            kind="json_feed",
            parser_profile="generic",
            is_active=True,
        )
        s.add(source)
        s.flush()
        run = IngestionRunModel(
            source_id=source.id,
            status="completed",
            documents_found=1,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        s.add(run)
        s.flush()
        stale_at = datetime.now(timezone.utc) - timedelta(days=120)
        s.add(
            SourceDocumentModel(
                source_id=source.id,
                ingestion_run_id=run.id,
                external_url="https://example.com/old",
                title="old",
                summary="",
                payload_json="{}",
                normalized_key="old",
                captured_at=stale_at,
            )
        )
        s.commit()
    finally:
        s.close()

    payload = _run_cli(["prune-source-documents", "--older-than-days", "90"])
    assert payload["dry_run"] is False
    assert payload["deleted"] == 1

    verify = db_session.SessionLocal()
    try:
        count = verify.scalar(select(func.count()).select_from(SourceDocumentModel))
        assert count == 0
    finally:
        verify.close()
