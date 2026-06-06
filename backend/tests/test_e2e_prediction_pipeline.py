"""End-to-end pipeline test: ingest a synthetic source, train, score.

The pieces in between routinely change shape (parser_profile keys,
artifact blend weights, knockout adjustments, anchor gates). A unit
test on any one of them won't catch a regression like the
parser_profile silent-fallback bug we lost an hour to on 2026-05-28
— where football_data_uk_csv sources were tagged with a non-existent
profile and the generic parser dropped fixtures, leaving zero matches
persisted while every individual layer passed its own tests.

This test exercises the full chain:
    register source → ingest documents → persist matches+results →
    train artifact → score a fresh match.

If any layer drops data on the floor the prediction surface degrades
in a way unit-test stubs can't see, so this is the canary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def _make_session(tmp_path):
    """Match the pattern from test_ingestion_persists_all_data.py:
    create the schema first, then apply migration alters. Using
    configure_session + run_migrations alone races with cleanup
    migrations that DELETE from tables which haven't been CREATE'd
    yet."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.migrations import run_migrations
    from app.models import tables  # noqa: F401 - register ORM models

    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return SessionLocal()


def _synthetic_csv_source_docs():
    """Build the document shape a CSV-style connector would emit, so we
    exercise the parser → match-creation path without depending on
    external HTTP. Each fixture has a final score; the parser must
    convert it to a historical_results row and the ingest service
    must persist it as a MatchResultModel + MatchModel."""
    from app.connectors.base import SourceDocument

    # Fixtures rotate between three teams so each (home, away) identity
    # is well outside the 14-day nearby-match tolerance window. Two
    # consecutive entries with the SAME identity 14d apart would merge
    # because the tolerance comparison is inclusive (<=) on both edges
    # — a pre-existing edge case in _find_nearby_match_for_result that
    # this test takes care to avoid.
    base = datetime(2025, 9, 1, 15, 0, tzinfo=timezone.utc)
    fixtures = [
        ("Alpha", "Bravo", 2, 1, 0),
        ("Bravo", "Charlie", 1, 1, 7),
        ("Charlie", "Alpha", 0, 2, 14),
        ("Alpha", "Charlie", 3, 0, 21),
        ("Bravo", "Alpha", 1, 2, 28),
        ("Charlie", "Bravo", 2, 3, 35),
        ("Alpha", "Bravo", 4, 1, 42),
        ("Bravo", "Charlie", 1, 2, 49),
        ("Charlie", "Alpha", 2, 2, 56),
        ("Alpha", "Charlie", 0, 1, 63),
        ("Bravo", "Alpha", 3, 1, 70),
        ("Charlie", "Bravo", 1, 0, 77),
        ("Alpha", "Bravo", 2, 0, 84),
        ("Bravo", "Charlie", 0, 0, 91),
    ]
    docs = []
    for home, away, hg, ag, offset in fixtures:
        played = (base + timedelta(days=offset)).isoformat()
        docs.append(
            SourceDocument(
                source_name="E2E Synthetic",
                source_url="memory://synthetic",
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": f"E2E {home} vs {away}",
                    "summary": f"{home} vs {away}",
                    "headings": ["E2E", f"{home} vs {away}"],
                    "fixtures": [
                        {
                            "competition": "E2E League",
                            "home_team": home,
                            "away_team": away,
                            "played_at": played,
                            "home_goals": hg,
                            "away_goals": ag,
                        }
                    ],
                },
            )
        )
    return docs


def test_full_pipeline_ingest_train_predict(tmp_path, monkeypatch) -> None:
    """Full happy-path: docs → matches → trained artifact → scored
    prediction.

    Asserts (in pipeline order):
      1. matches were persisted from the parsed fixtures
      2. match_results landed too (so training has labels)
      3. the trained artifact carries the new blend defaults
      4. scoring a fresh match returns a valid probability distribution
    """
    from app.connectors.base import SourceConnector
    from app.connectors.registry import connector_registry
    from app.models.tables import MatchModel, MatchResultModel, SourceModel
    from app.parsers.registry import parser_registry
    from app.repositories.entity_repository import EntityRepository
    from app.repositories.ingestion_repository import IngestionRepository
    from app.repositories.result_repository import ResultRepository
    from app.repositories.training_repository import TrainingRepository
    from app.services.ingestion_service import IngestionService
    from app.services.model_training_service import ModelTrainingService
    from sqlalchemy import select, func

    parser_registry.reset()
    connector_registry.clear()
    session = _make_session(tmp_path)
    try:
        # Register the synthetic source so the SourceModel row exists.
        # We don't need the real connector to talk to HTTP — we patch
        # the connector factory to hand back our prebuilt SourceConnector
        # that returns synthetic documents directly.
        source = SourceModel(
            name="E2E Synthetic",
            base_url="memory://synthetic",
            kind="json_feed",
            parser_profile="sports_feed_v1",
            is_active=True,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        class _MemoryConnector(SourceConnector):
            name = "E2E Synthetic"
            kind = "json_feed"

            def fetch(self):
                return _synthetic_csv_source_docs()

            def metadata(self):
                from app.connectors.base import ConnectorMetadata

                return ConnectorMetadata(name=self.name, kind=self.kind)

        connector_registry.register(_MemoryConnector())

        ingest = IngestionService(IngestionRepository(session))
        run = ingest.run_for_source(source.id)
        assert run.status == "completed", f"ingest failed: {run.error_message!r}"
        assert run.documents_found == 14

        # (1) matches were persisted
        match_count = session.scalar(select(func.count()).select_from(MatchModel))
        if match_count != 14:
            ms = session.scalars(select(MatchModel)).all()
            details = [
                f"{m.kickoff_at.date()} {m.home_team.name} vs {m.away_team.name}"
                for m in ms
            ]
            assert False, f"expected 14 matches, got {match_count}\n  rows={details}"

        # (2) results landed (so training has labels)
        result_count = session.scalar(select(func.count()).select_from(MatchResultModel))
        assert result_count == 14, f"expected 14 results, got {result_count}"

        # (3) train and inspect the artifact
        training = ModelTrainingService(
            TrainingRepository(session),
            EntityRepository(session),
            ResultRepository(session),
        )
        training.train("e2e_model")
        artifact = training.latest_artifact("e2e_model")
        assert artifact is not None
        # New defaults from F1.5 (2026-05-28 blend rebalance)
        weights = artifact.get("blend_weights") or {}
        assert weights.get("elo") == 0.30
        assert weights.get("profile") == 0.45
        # Heuristic baseline must include team profiles for all 3 teams
        team_profiles = artifact.get("team_profiles") or {}
        assert len(team_profiles) >= 3, f"expected 3 team profiles, got {team_profiles!r}"

        # (4) score a fresh match. Build one in-memory with the same
        # competition + team identities. We don't persist it — the
        # heuristic scorer reads team names off the match model.
        fresh_match = SimpleNamespace(
            id="e2e-prediction-target",
            competition=SimpleNamespace(name="E2E League", country="Europe", season="2025-2026"),
            home_team=SimpleNamespace(name="Alpha", country="Europe"),
            away_team=SimpleNamespace(name="Bravo", country="Europe"),
            kickoff_at=datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc),
            venue=None,
            evidence_items=[],
        )
        scored = training.score_match(fresh_match)
        # Probability distribution sanity: three classes, sum ~1, all in [0,1].
        assert set(scored.keys()) == {"home", "draw", "away"}
        assert all(0.0 <= v <= 1.0 for v in scored.values())
        # Scored values are rounded to 3dp before return, so a strict
        # 1e-6 tolerance would fail on rounding alone. Accept 1e-2.
        assert abs(sum(scored.values()) - 1.0) < 1e-2
    finally:
        session.close()
        parser_registry.reset()
        connector_registry.clear()


def test_unregistered_parser_profile_emits_warning(tmp_path, monkeypatch, caplog) -> None:
    """Regression for the silent-fallback bug fixed in F1.2.

    When a source row carries a parser_profile that isn't registered
    (typo, code drift, etc.) the registry must emit a WARNING — not
    silently return the generic parser, because generic drops
    `fixtures` payloads and no matches will be persisted."""
    import logging

    from app.parsers.registry import parser_registry

    parser_registry.reset()
    with caplog.at_level(logging.WARNING, logger="app.parsers.registry"):
        parser = parser_registry.get("nonexistent_profile_xyz")
    # Fallback is still functional so existing call sites don't crash;
    # the warning is the contract.
    assert parser.profile_name == "generic"
    assert any(
        "parser_profile 'nonexistent_profile_xyz'" in record.getMessage()
        and "falling back" in record.getMessage().lower()
        for record in caplog.records
    ), f"missing fallback warning, saw: {[r.getMessage() for r in caplog.records]}"
