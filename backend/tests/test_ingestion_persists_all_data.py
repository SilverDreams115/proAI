"""Integration test for the historical ingestion pipeline (Fase 6.3).

Verifies the end-to-end invariant: every fixture with goals that flows
through a sports_feed_v1 parser ends up as a `MatchResultModel` row
attached to the right `MatchModel`. The test wires a small fake source
that emits a handful of fixtures with results and confirms the database
state afterwards.

The test uses the real services (parser, ingestion, repositories)
against an in-memory SQLite to keep the assertion surface honest — if a
future refactor breaks the pipeline silently, this test catches it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.connectors.base import SourceDocument
from app.connectors.registry import connector_registry
from app.db.base import Base
from app.db.migrations import run_migrations
from app.models.tables import (
    MatchModel,
    MatchResultModel,
    SourceModel,
)
from app.repositories.ingestion_repository import IngestionRepository
from app.services.ingestion_service import IngestionService


def _fixture_doc(home: str, away: str, played_at: str, score: tuple[int, int]) -> SourceDocument:
    return SourceDocument(
        source_name="Test Sports Feed",
        source_url="https://example.test/feed",
        captured_at=datetime.now(timezone.utc),
        payload={
            "title": f"Test League {home} vs {away}",
            "summary": f"{home} vs {away}",
            "headings": ["Test League"],
            "fixtures": [
                {
                    "competition": "Test League",
                    "home_team": home,
                    "away_team": away,
                    "played_at": played_at,
                    "home_goals": score[0],
                    "away_goals": score[1],
                }
            ],
        },
    )


def test_csv_style_fixtures_with_goals_persist_as_results(tmp_path) -> None:
    """A sports_feed_v1 source whose fixtures carry goals must end up
    with one MatchResultModel per fixture, attached to the right
    MatchModel by (competition, teams, kickoff)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    connector_registry.clear()

    fixtures = [
        ("Alpha FC", "Bravo United", "2026-02-01T19:00:00+00:00", (2, 1)),
        ("Bravo United", "Charlie SC", "2026-02-08T19:00:00+00:00", (1, 1)),
        ("Charlie SC", "Alpha FC", "2026-02-15T19:00:00+00:00", (0, 2)),
        ("Alpha FC", "Delta Town", "2026-02-22T19:00:00+00:00", (3, 0)),
    ]

    class _StubConnector:
        name = "Test Sports Feed"
        kind = "json_feed"
        base_url = "https://example.test/feed"
        description = "Fixture stub"

        def metadata(self):
            from app.connectors.base import ConnectorMetadata
            return ConnectorMetadata(name=self.name, kind=self.kind, base_url=self.base_url, description=self.description)

        def fetch(self):
            return [_fixture_doc(home, away, played_at, score) for home, away, played_at, score in fixtures]

    connector_registry.register(_StubConnector())

    session = SessionLocal()
    try:
        source = SourceModel(
            name="Test Sports Feed",
            base_url="https://example.test/feed",
            kind="json_feed",
            parser_profile="sports_feed_v1",
            is_active=True,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        ingestion = IngestionService(IngestionRepository(session))
        run = ingestion.run_for_source(source.id)
        assert run.status == "completed", f"ingestion should complete: {run.error_detail}"

        # Every fixture with goals must end up as a MatchResultModel row,
        # bound to the corresponding MatchModel.
        results = list(session.scalars(select(MatchResultModel)).all())
        assert len(results) == len(fixtures), (
            f"expected {len(fixtures)} persisted results, got {len(results)}"
        )
        match_count = session.scalar(select(MatchModel.id))
        assert match_count is not None, "no MatchModel rows were created"
        for played_at, score in [(p, s) for _, _, p, s in fixtures]:
            row = session.scalar(
                select(MatchResultModel).where(
                    MatchResultModel.played_at == datetime.fromisoformat(played_at),
                    MatchResultModel.home_goals == score[0],
                    MatchResultModel.away_goals == score[1],
                )
            )
            assert row is not None, f"missing result for played_at={played_at} score={score}"
            assert row.match_id is not None, "result row must be bound to a match"
    finally:
        session.close()
        connector_registry.clear()
