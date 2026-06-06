"""Tests for composition_hash backfill and _auto_discover_slates week_type guard.

Covers:
  - backfill_composition_hashes() fills NULL hashes from DB match data.
  - backfill is idempotent (second call updates 0 slates).
  - backfill never invalidates existing valid snapshots.
  - PG-2336-like slate has hash + version=1 after backfill.
  - _auto_discover_slates skips a draw_code whose existing slate has a
    different week_type, logs the conflict, and leaves the slate unchanged.
  - _auto_discover_slates proceeds normally when week_type matches.
  - latest_for_slate is not affected by backfill (no spurious invalidation).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.slate_repository import SlateRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.schemas.slate_discovery import SlateDiscoveryResponse, DiscoveredSlateMatchResponse


# ---------------------------------------------------------------------------
# Helpers (shared with test_slate_composition_hash pattern)
# ---------------------------------------------------------------------------

def _match(
    position: int,
    home: str,
    away: str,
    kickoff: str = "2026-06-15T15:00:00+00:00",
    competition: str = "Liga MX",
) -> MatchReferencePayload:
    return MatchReferencePayload(
        position=position,
        competition=CompetitionPayload(name=competition),
        home_team=TeamPayload(name=home),
        away_team=TeamPayload(name=away),
        kickoff_at=datetime.fromisoformat(kickoff),
    )


def _slate(
    draw_code: str,
    matches: list[MatchReferencePayload],
    week_type: str = "weekend",
) -> ProgolSlateCreate:
    return ProgolSlateCreate(
        label=f"Test {draw_code}",
        draw_code=draw_code,
        week_type=week_type,
        registration_closes_at=datetime(2026, 12, 31, 3, 0, tzinfo=timezone.utc),
        matches=matches,
    )


def _matches_v1(n: int = 14, tag: str = "Alpha") -> list[MatchReferencePayload]:
    return [_match(i, f"{tag}Home{i}", f"{tag}Away{i}") for i in range(1, n + 1)]


def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'backfill_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Tests: backfill_composition_hashes()
# ---------------------------------------------------------------------------

def test_backfill_fills_null_hash(db):
    """backfill_composition_hashes() sets composition_hash for a NULL-hash slate."""
    repo = SlateRepository(db)

    slate = repo.upsert_slate(_slate("PG-BACK1", _matches_v1()))
    db.commit()

    # Manually clear the hash to simulate pre-v10 state.
    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-BACK1'")
    )
    db.commit()

    # Confirm it's NULL now.
    null_hash = db.execute(
        text("SELECT composition_hash FROM progol_slates WHERE draw_code = 'PG-BACK1'")
    ).scalar_one()
    assert null_hash is None

    count = repo.backfill_composition_hashes()
    db.commit()

    assert count == 1
    new_hash = db.execute(
        text("SELECT composition_hash FROM progol_slates WHERE draw_code = 'PG-BACK1'")
    ).scalar_one()
    assert new_hash is not None
    assert len(new_hash) == 64


def test_backfill_is_idempotent(db):
    """Calling backfill twice leaves count=0 on second call."""
    repo = SlateRepository(db)

    repo.upsert_slate(_slate("PG-BACK2", _matches_v1()))
    db.commit()

    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-BACK2'")
    )
    db.commit()

    first = repo.backfill_composition_hashes()
    db.commit()
    assert first == 1

    second = repo.backfill_composition_hashes()
    db.commit()
    assert second == 0


def test_backfill_multiple_null_slates(db):
    """backfill_composition_hashes() handles multiple NULL-hash slates at once."""
    repo = SlateRepository(db)

    for code in ("PG-M1", "PG-M2", "PG-M3"):
        repo.upsert_slate(_slate(code, _matches_v1(tag=code)))
        db.commit()

    db.execute(text("UPDATE progol_slates SET composition_hash = NULL"))
    db.commit()

    count = repo.backfill_composition_hashes()
    db.commit()
    assert count == 3

    rows = db.execute(
        text("SELECT composition_hash FROM progol_slates ORDER BY draw_code")
    ).fetchall()
    for (h,) in rows:
        assert h is not None
        assert len(h) == 64


def test_backfill_does_not_invalidate_snapshots(db):
    """Backfill must NOT invalidate any existing valid snapshots."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-BACK3", _matches_v1()))
    db.commit()
    ticket_repo.save_snapshot(
        slate_id=slate.id,
        model_version="v1",
        payload={"test": True},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-BACK3'")
    )
    db.commit()

    repo.backfill_composition_hashes()
    db.commit()

    still_valid = ticket_repo.latest_for_slate(slate.id)
    assert still_valid is not None
    assert still_valid.is_valid is True


def test_backfill_pg2336_pattern(db):
    """A PG-2336-like slate (14 future matches, no snapshots) ends up with
    version=1 and a 64-char hash after backfill — ready for predictions."""
    repo = SlateRepository(db)

    international = [
        _match(
            i,
            f"NationA{i}",
            f"NationB{i}",
            kickoff="2026-06-12T20:00:00+00:00",
            competition="International Friendlies",
        )
        for i in range(1, 15)
    ]
    slate = repo.upsert_slate(_slate("PG-2336-SIM", international))
    db.commit()

    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-2336-SIM'")
    )
    db.commit()

    count = repo.backfill_composition_hashes()
    db.commit()
    assert count == 1

    db.expire_all()
    slate_after = repo.find_by_draw_code("PG-2336-SIM")
    assert slate_after is not None
    assert slate_after.composition_hash is not None
    assert len(slate_after.composition_hash) == 64
    assert slate_after.slate_version == 1


def test_backfill_skips_slate_without_matches(db):
    """A slate with no match links (edge case) is skipped by backfill."""
    repo = SlateRepository(db)

    slate = repo.upsert_slate(_slate("PG-NOMATCH", _matches_v1()))
    db.commit()

    # Delete all slate match links to simulate an empty slate, then clear hash.
    db.execute(text(f"DELETE FROM progol_slate_matches WHERE slate_id = '{slate.id}'"))
    db.execute(
        text("UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-NOMATCH'")
    )
    db.commit()

    count = repo.backfill_composition_hashes()
    db.commit()
    assert count == 0  # skipped because no matches


# ---------------------------------------------------------------------------
# Tests: _auto_discover_slates week_type guard
# ---------------------------------------------------------------------------

def test_auto_discover_skips_draw_code_with_different_week_type(tmp_path, caplog):
    """_auto_discover_slates must not overwrite an existing slate's week_type.

    Scenario:
      - Slate PG-9000 was promoted as week_type='midweek'.
      - Auto-discovery would infer it as week_type='weekend' from the same source.
      - The guard must skip it, log a warning, and leave the slate untouched.
    """
    from app.services.ingestion_service import IngestionService

    engine = _setup_engine(tmp_path)

    with Session(engine) as session:
        slate_repo = SlateRepository(session)
        # Create an existing midweek slate with a known draw_code.
        existing = slate_repo.upsert_slate(_slate("PG-9000", _matches_v1(), week_type="midweek"))
        session.commit()
        original_version = existing.slate_version
        original_hash = existing.composition_hash

        # Build a minimal stub IngestionRepository.
        from app.repositories.ingestion_repository import IngestionRepository

        mock_ingestion_repo = MagicMock(spec=IngestionRepository)
        mock_ingestion_repo.session = session

        svc = IngestionService(repository=mock_ingestion_repo)

        # Patch SlateDiscoveryService.discover to return a response claiming
        # the same draw_code but with week_type='weekend'.
        weekend_response = SlateDiscoveryResponse(
            label="Progol 9000",
            draw_code="PG-9000",
            week_type="weekend",
            match_target=14,
            matches=[
                DiscoveredSlateMatchResponse(
                    position=i,
                    competition=CompetitionPayload(name="Liga MX"),
                    home_team=TeamPayload(name=f"Home{i}"),
                    away_team=TeamPayload(name=f"Away{i}"),
                    kickoff_at=datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc),
                )
                for i in range(1, 15)
            ],
        )

        with caplog.at_level(logging.WARNING), patch(
            "app.services.ingestion_service.SlateDiscoveryService.discover",
            return_value=weekend_response,
        ) as mock_discover:
            svc._auto_discover_slates("source-123")

        # discover() must have been called exactly once per week_type iteration
        # (dry-run only). The persist phase must have been skipped for "weekend"
        # because of the week_type conflict.
        # For "midweek" and "revancha", discover also returns the weekend_response,
        # so the guard compares existing.week_type="midweek" vs response.week_type="weekend"
        # for all three — all three are skipped.
        assert mock_discover.call_count == 3  # one dry-run per week_type, no persist calls

        # The existing slate must be unchanged.
        session.expire_all()
        unchanged = slate_repo.find_by_draw_code("PG-9000")
        assert unchanged is not None
        assert unchanged.week_type == "midweek"
        assert unchanged.slate_version == original_version
        assert unchanged.composition_hash == original_hash

    # A warning must have been logged.
    assert any("auto_discover_week_type_conflict" in r.message for r in caplog.records)


def test_auto_discover_proceeds_when_week_type_matches(tmp_path):
    """_auto_discover_slates calls persist when existing week_type matches."""
    from app.services.ingestion_service import IngestionService
    from app.repositories.ingestion_repository import IngestionRepository

    engine = _setup_engine(tmp_path)

    with Session(engine) as session:
        slate_repo = SlateRepository(session)
        # Create an existing weekend slate.
        existing = slate_repo.upsert_slate(_slate("PG-8000", _matches_v1(), week_type="weekend"))
        session.commit()

        mock_ingestion_repo = MagicMock(spec=IngestionRepository)
        mock_ingestion_repo.session = session

        svc = IngestionService(repository=mock_ingestion_repo)

        # Dry-run returns same draw_code + same week_type.
        weekend_response = SlateDiscoveryResponse(
            label="Progol 8000",
            draw_code="PG-8000",
            week_type="weekend",
            match_target=14,
            matches=[],
        )

        with patch(
            "app.services.ingestion_service.SlateDiscoveryService.discover",
            return_value=weekend_response,
        ) as mock_discover:
            svc._auto_discover_slates("source-456")

        # Mock always returns week_type="weekend"; existing slate is "weekend" too.
        # So all three week_type iterations see no conflict and each does 2 calls
        # (dry-run + persist): 3 * 2 = 6.
        assert mock_discover.call_count == 6


# ---------------------------------------------------------------------------
# Tests: latest_for_slate not affected by backfill
# ---------------------------------------------------------------------------

def test_latest_for_slate_unaffected_by_backfill(db):
    """Backfill sets composition_hash without disturbing is_valid on snapshots."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-STABLE2", _matches_v1()))
    db.commit()

    for i in range(3):
        ticket_repo.save_snapshot(
            slate_id=slate.id,
            model_version=f"v{i}",
            payload={"i": i},
            composition_hash=slate.composition_hash,
        )
    db.commit()

    db.execute(
        text(
            "UPDATE progol_slates SET composition_hash = NULL WHERE draw_code = 'PG-STABLE2'"
        )
    )
    db.commit()

    repo.backfill_composition_hashes()
    db.commit()

    # All 3 snapshots still valid; latest_for_slate returns the newest.
    latest = ticket_repo.latest_for_slate(slate.id)
    assert latest is not None
    assert latest.is_valid is True

    invalid_count = db.execute(
        text(
            "SELECT COUNT(*) FROM ticket_recommendation_snapshots "
            "WHERE slate_id = :sid AND is_valid = 0"
        ),
        {"sid": slate.id},
    ).scalar_one()
    assert invalid_count == 0
