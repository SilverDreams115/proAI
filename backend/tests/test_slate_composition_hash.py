"""Tests for slate composition hash tracking and snapshot invalidation.

Covers:
  - Same draw_code + same fixtures → no version bump, no snapshot invalidation.
  - Same draw_code + different fixtures → version bumps, valid snapshots invalidated.
  - latest_for_slate() never returns an invalidated snapshot.
  - New slate starts with version=1 and a 64-char hex hash.
  - Reproduction of the PG-2334 pattern: 5 snapshots saved, composition
    changes, all 5 become invalid, new post-change snapshot is valid.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.repositories.slate_repository import SlateRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate


# ---------------------------------------------------------------------------
# Helpers
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
        registration_closes_at=datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc),
        matches=matches,
    )


def _matches_v1(n: int = 14, tag: str = "Alpha") -> list[MatchReferencePayload]:
    return [_match(i, f"{tag}Home{i}", f"{tag}Away{i}") for i in range(1, n + 1)]


def _matches_v2(n: int = 14, tag: str = "Beta") -> list[MatchReferencePayload]:
    return [_match(i, f"{tag}Home{i}", f"{tag}Away{i}") for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Fixture: isolated SQLite DB per test
# ---------------------------------------------------------------------------

def _setup_engine(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'composition_test.db'}")
    run_migrations(db_mod.engine)
    return db_mod.engine


@pytest.fixture
def db(tmp_path):
    engine = _setup_engine(tmp_path)
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Tests: composition_hash computation
# ---------------------------------------------------------------------------

def test_compute_composition_hash_is_deterministic():
    """Same payload → same hash, different payload → different hash."""
    matches = _matches_v1()
    p1 = _slate("PG-X", matches)
    p2 = _slate("PG-X", matches)
    p_other = _slate("PG-X", _matches_v2())

    h1 = SlateRepository._compute_composition_hash(p1)
    h2 = SlateRepository._compute_composition_hash(p2)
    h_other = SlateRepository._compute_composition_hash(p_other)

    assert h1 == h2
    assert h1 != h_other
    assert len(h1) == 64  # SHA-256 hex digest


def test_compute_composition_hash_includes_draw_code():
    """Same fixtures but different draw_code must produce different hashes."""
    matches = _matches_v1()
    h1 = SlateRepository._compute_composition_hash(_slate("PG-100", matches))
    h2 = SlateRepository._compute_composition_hash(_slate("PG-200", matches))
    assert h1 != h2


def test_compute_composition_hash_normalises_case():
    """Team names are case-folded before hashing — 'Pumas' == 'PUMAS'."""
    m_lower = [_match(1, "pumas", "tigres")]
    m_upper = [_match(1, "PUMAS", "TIGRES")]
    h_lower = SlateRepository._compute_composition_hash(_slate("PG-1", m_lower))
    h_upper = SlateRepository._compute_composition_hash(_slate("PG-1", m_upper))
    assert h_lower == h_upper


# ---------------------------------------------------------------------------
# Tests: upsert_slate lifecycle
# ---------------------------------------------------------------------------

def test_new_slate_gets_version_1_and_hash(db):
    """A freshly inserted slate starts at version=1 with a valid hash."""
    repo = SlateRepository(db)
    slate = repo.upsert_slate(_slate("PG-NEW", _matches_v1()))
    db.commit()

    assert slate.slate_version == 1
    assert slate.composition_hash is not None
    assert len(slate.composition_hash) == 64


def test_upsert_same_composition_does_not_bump_version(db):
    """Re-upserting an identical payload must not change version or hash."""
    repo = SlateRepository(db)
    payload = _slate("PG-STABLE", _matches_v1())

    slate1 = repo.upsert_slate(payload)
    db.commit()
    original_hash = slate1.composition_hash
    original_version = slate1.slate_version

    slate2 = repo.upsert_slate(payload)
    db.commit()

    assert slate2.id == slate1.id
    assert slate2.composition_hash == original_hash
    assert slate2.slate_version == original_version


def test_upsert_different_composition_bumps_version(db):
    """Different fixtures for the same draw_code must increment slate_version."""
    repo = SlateRepository(db)

    slate_v1 = repo.upsert_slate(_slate("PG-CHANGE", _matches_v1()))
    db.commit()
    assert slate_v1.slate_version == 1
    # Capture hash NOW (after commit forces a DB reload) before the next
    # upsert modifies the in-memory object via SQLAlchemy's identity map.
    hash_v1 = str(slate_v1.composition_hash)
    slate_id = slate_v1.id

    slate_v2 = repo.upsert_slate(_slate("PG-CHANGE", _matches_v2()))
    db.commit()

    assert slate_v2.id == slate_id
    assert slate_v2.slate_version == 2
    assert slate_v2.composition_hash != hash_v1


def test_multiple_composition_changes_increment_version_correctly(db):
    """Three different compositions → version 1, 2, 3."""
    repo = SlateRepository(db)
    draw = "PG-TRIPLE"

    # Capture each version immediately after commit so the next upsert's
    # in-memory mutation (via SQLAlchemy's identity map) doesn't overwrite
    # the value before we read it.
    repo.upsert_slate(_slate(draw, _matches_v1(tag="Set1")))
    db.commit()
    v1 = db.execute(
        __import__("sqlalchemy", fromlist=["text"]).text(
            "SELECT slate_version FROM progol_slates WHERE draw_code = :dc"
        ),
        {"dc": draw},
    ).scalar_one()

    repo.upsert_slate(_slate(draw, _matches_v1(tag="Set2")))
    db.commit()
    v2 = db.execute(
        __import__("sqlalchemy", fromlist=["text"]).text(
            "SELECT slate_version FROM progol_slates WHERE draw_code = :dc"
        ),
        {"dc": draw},
    ).scalar_one()

    repo.upsert_slate(_slate(draw, _matches_v1(tag="Set3")))
    db.commit()
    v3 = db.execute(
        __import__("sqlalchemy", fromlist=["text"]).text(
            "SELECT slate_version FROM progol_slates WHERE draw_code = :dc"
        ),
        {"dc": draw},
    ).scalar_one()

    assert v1 == 1
    assert v2 == 2
    assert v3 == 3


# ---------------------------------------------------------------------------
# Tests: snapshot invalidation
# ---------------------------------------------------------------------------

def test_composition_change_invalidates_valid_snapshots(db):
    """After a composition change, all previously valid snapshots become invalid."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-INV", _matches_v1()))
    db.commit()

    # Save 3 valid snapshots
    for i in range(3):
        ticket_repo.save_snapshot(
            slate_id=slate.id,
            model_version=f"v{i}",
            payload={"idx": i},
            composition_hash=slate.composition_hash,
        )
    db.commit()

    # Re-upsert with different fixtures
    repo.upsert_slate(_slate("PG-INV", _matches_v2()))
    db.commit()

    # No valid snapshots should remain
    assert ticket_repo.latest_for_slate(slate.id) is None


def test_composition_change_sets_invalidated_at_and_reason(db):
    """Invalidated snapshots must carry a timestamp and a reason string."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-AUDIT", _matches_v1()))
    db.commit()
    ticket_repo.save_snapshot(
        slate_id=slate.id,
        model_version="v1",
        payload={},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    repo.upsert_slate(_slate("PG-AUDIT", _matches_v2()))
    db.commit()

    stale = ticket_repo.latest_for_slate_any(slate.id)
    assert stale is not None
    assert stale.is_valid is False
    assert stale.invalidated_at is not None
    assert stale.invalidation_reason is not None
    assert "composition_changed_from_" in stale.invalidation_reason


def test_same_composition_does_not_invalidate_snapshots(db):
    """Re-ingesting the same fixtures must leave valid snapshots untouched."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    payload = _slate("PG-KEEPVALID", _matches_v1())
    slate = repo.upsert_slate(payload)
    db.commit()
    ticket_repo.save_snapshot(
        slate_id=slate.id,
        model_version="v1",
        payload={},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    # Re-upsert same fixtures
    repo.upsert_slate(payload)
    db.commit()

    valid = ticket_repo.latest_for_slate(slate.id)
    assert valid is not None
    assert valid.is_valid is True


# ---------------------------------------------------------------------------
# Tests: snapshot lifecycle around latest_for_slate
# ---------------------------------------------------------------------------

def test_latest_for_slate_returns_none_when_all_invalid(db):
    """latest_for_slate must return None when only stale snapshots exist."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-NONELEFT", _matches_v1()))
    db.commit()
    ticket_repo.save_snapshot(
        slate_id=slate.id,
        model_version="v1",
        payload={},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    # Trigger invalidation
    repo.upsert_slate(_slate("PG-NONELEFT", _matches_v2()))
    db.commit()

    assert ticket_repo.latest_for_slate(slate.id) is None


def test_new_snapshot_after_composition_change_is_valid(db):
    """A snapshot generated after a composition change must be valid."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    slate = repo.upsert_slate(_slate("PG-NEWSNAP", _matches_v1()))
    db.commit()
    ticket_repo.save_snapshot(
        slate_id=slate.id, model_version="v1", payload={},
        composition_hash=slate.composition_hash,
    )
    db.commit()

    slate_v2 = repo.upsert_slate(_slate("PG-NEWSNAP", _matches_v2()))
    db.commit()

    ticket_repo.save_snapshot(
        slate_id=slate_v2.id, model_version="v2", payload={},
        composition_hash=slate_v2.composition_hash,
    )
    db.commit()

    valid = ticket_repo.latest_for_slate(slate_v2.id)
    assert valid is not None
    assert valid.is_valid is True
    assert valid.composition_hash == slate_v2.composition_hash


# ---------------------------------------------------------------------------
# Tests: PG-2334 reproduction scenario
# ---------------------------------------------------------------------------

def test_pg2334_pattern_five_snapshots_invalidated_on_composition_change(db):
    """Reproduce the PG-2334 desacoplamiento: slate gets several snapshots,
    then a completely different fixture set is loaded. All prior snapshots
    must become invalid; a fresh snapshot generated after the change is valid."""
    repo = SlateRepository(db)
    ticket_repo = TicketRecommendationRepository(db)

    # Initial load: Liga MX / LaLiga fixtures
    liga_mx_fixtures = _matches_v1(tag="LigaMX")
    slate = repo.upsert_slate(_slate("PG-2334-SIM", liga_mx_fixtures))
    db.commit()
    hash_v1 = slate.composition_hash

    for i in range(5):
        ticket_repo.save_snapshot(
            slate_id=slate.id,
            model_version=f"model-{i}",
            payload={"snapshot_index": i},
            composition_hash=hash_v1,
        )
    db.commit()

    # Composition change: Libertadores fixtures replace the Liga MX ones
    libertadores_fixtures = _matches_v1(tag="Libertadores")
    slate_v2 = repo.upsert_slate(_slate("PG-2334-SIM", libertadores_fixtures))
    db.commit()

    assert slate_v2.id == slate.id
    assert slate_v2.slate_version == 2
    assert slate_v2.composition_hash != hash_v1

    # All 5 prior snapshots must be invalid
    assert ticket_repo.latest_for_slate(slate.id) is None

    # New snapshot after the composition change must be valid
    ticket_repo.save_snapshot(
        slate_id=slate_v2.id,
        model_version="model-post-change",
        payload={"slate_id": slate_v2.id},
        composition_hash=slate_v2.composition_hash,
    )
    db.commit()

    fresh = ticket_repo.latest_for_slate(slate_v2.id)
    assert fresh is not None
    assert fresh.is_valid is True
    assert fresh.composition_hash == slate_v2.composition_hash


# ---------------------------------------------------------------------------
# Tests: PG-2336 active slate readiness
# ---------------------------------------------------------------------------

def test_pg2336_clean_slate_starts_valid(db):
    """A slate loaded for the first time (no prior snapshots, no prior composition)
    starts with version=1 and a 64-char hash — ready for clean predictions."""
    repo = SlateRepository(db)
    international_fixtures = [
        _match(i, f"NationA{i}", f"NationB{i}", competition="International Friendlies")
        for i in range(1, 15)
    ]
    slate = repo.upsert_slate(_slate("PG-2336-SIM", international_fixtures))
    db.commit()

    assert slate.slate_version == 1
    assert slate.composition_hash is not None
    assert len(slate.composition_hash) == 64
    # No snapshots yet, clean state
    from app.repositories.ticket_repository import TicketRecommendationRepository
    assert TicketRecommendationRepository(db).latest_for_slate(slate.id) is None
