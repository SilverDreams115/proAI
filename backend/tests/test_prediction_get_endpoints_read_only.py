"""Regression: prediction GET endpoints must be strictly read-only.

The snapshot drift observed during R5.3 (match_feature_snapshots 1110 -> 1124)
was caused by the live API serving GET requests for PG-2338 while the feature
cache was expired: ``FeatureService.build_match_features`` lazily persisted a
fresh snapshot on every cache miss. These tests pin the contract that

  GET /api/predictions/slates/{id}/features
  GET /api/predictions/slates/{id}/quality
  GET /api/predictions/slates/{id}/ticket

never grow ``match_feature_snapshots`` (or any other table), while an explicit
``persist=True`` refresh still may.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import MatchFeatureSnapshotModel
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload, MatchReferencePayload, TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.services.feature_service import FeatureService


# ---------------------------------------------------------------------------
# Unit level: the writer itself. build_match_features must not touch the
# session unless the caller explicitly asks to persist.
# ---------------------------------------------------------------------------


class _Snapshot:
    def __init__(self, *, generated_at: datetime, payload: dict, version: str) -> None:
        self.generated_at = generated_at
        self.payload_json = json.dumps(payload)
        self.feature_set_version = version


class _CountingSession:
    """Fails loudly if a read-only call tries to mutate the session."""

    def __init__(self, snapshot: _Snapshot | None) -> None:
        self._snapshot = snapshot
        self.info: dict = {}
        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0

    def scalar(self, _statement):
        return self._snapshot

    def add(self, _obj):
        self.add_calls += 1

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        pass


class _CountingRepo:
    def __init__(self, snapshot: _Snapshot | None, match) -> None:
        self.session = _CountingSession(snapshot)
        self._match = match
        self.save_snapshot_calls = 0

    def get_match(self, _match_id):
        return self._match

    def list_match_evidence(self, _match_id):
        return []

    def list_match_availability(self, _match_id):
        return []

    def count_evidence_items(self, _match_id):
        return 0

    def count_linked_documents(self, _match_id):
        return 0

    def save_snapshot(self, _match_id, _version, payload):
        self.save_snapshot_calls += 1
        return SimpleNamespace(
            generated_at=datetime.now(timezone.utc),
            payload_json=json.dumps(payload),
        )


@pytest.fixture
def fake_match() -> SimpleNamespace:
    return SimpleNamespace(
        id="match-ro-1",
        venue="Stadium",
        kickoff_at=datetime.now(timezone.utc) + timedelta(days=1),
        home_team=SimpleNamespace(name="Club A", country="MX"),
        away_team=SimpleNamespace(name="Club B", country="MX"),
        home_team_id="t-a",
        away_team_id="t-b",
    )


def test_read_only_cache_miss_does_not_persist(fake_match) -> None:
    """With no cached snapshot, the default (read-only) call recomputes in
    memory and never writes a snapshot row."""
    repo = _CountingRepo(None, fake_match)
    service = FeatureService(repo)

    _, payload, _generated_at = service.build_match_features(fake_match.id)

    assert repo.save_snapshot_calls == 0
    assert repo.session.add_calls == 0
    assert repo.session.flush_calls == 0
    assert repo.session.commit_calls == 0
    assert isinstance(payload, dict) and payload


def test_read_only_stale_cache_does_not_persist(fake_match) -> None:
    """An expired snapshot (the exact PG-2338 drift trigger) must be ignored
    for reads without rewriting a fresh one."""
    stale = _Snapshot(
        generated_at=datetime.now(timezone.utc)
        - timedelta(seconds=FeatureService.SNAPSHOT_TTL_SECONDS + 10),
        payload={"hours_to_kickoff": 999.0},
        version=FeatureService.FEATURE_SET_VERSION,
    )
    repo = _CountingRepo(stale, fake_match)
    service = FeatureService(repo)

    _, payload, _ = service.build_match_features(fake_match.id)

    assert repo.save_snapshot_calls == 0
    assert payload["hours_to_kickoff"] != 999.0


def test_explicit_persist_still_writes(fake_match) -> None:
    """The explicit, non-GET refresh path keeps the ability to persist."""
    repo = _CountingRepo(None, fake_match)
    service = FeatureService(repo)

    service.build_match_features(fake_match.id, use_cache=False, persist=True)

    assert repo.save_snapshot_calls == 1


def test_read_only_and_persist_payloads_match(fake_match) -> None:
    """Read-only output is structurally identical to the persisted payload."""
    ro_repo = _CountingRepo(None, fake_match)
    persist_repo = _CountingRepo(None, fake_match)

    _, ro_payload, _ = FeatureService(ro_repo).build_match_features(fake_match.id)
    _, persisted_payload, _ = FeatureService(persist_repo).build_match_features(
        fake_match.id, use_cache=False, persist=True
    )

    assert ro_payload == persisted_payload


# ---------------------------------------------------------------------------
# Endpoint level: hit the real GET routes against a real (sqlite) DB and prove
# the snapshot count never moves.
# ---------------------------------------------------------------------------


def _slate_payload(draw_code: str, *, count: int = 14) -> ProgolSlateCreate:
    kickoff = datetime(2026, 6, 24, 20, 0, tzinfo=timezone.utc)
    return ProgolSlateCreate(
        label=f"Progol {draw_code}",
        draw_code=draw_code,
        week_type="weekend",
        registration_closes_at=kickoff - timedelta(hours=1),
        matches=[
            MatchReferencePayload(
                position=idx,
                competition=CompetitionPayload(name="International Friendlies"),
                home_team=TeamPayload(name=f"{draw_code}-Home-{idx}"),
                away_team=TeamPayload(name=f"{draw_code}-Away-{idx}"),
                kickoff_at=kickoff + timedelta(hours=idx),
            )
            for idx in range(1, count + 1)
        ],
    )


def _snapshot_count(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0


@pytest.mark.anyio
async def test_get_endpoints_do_not_grow_feature_snapshots(client) -> None:
    from app.db import session as db_mod

    engine = db_mod.engine
    with Session(engine) as session:
        slate = SlateRepository(session).upsert_slate(_slate_payload("PG-RO-1", count=4))
        session.commit()
        slate_id = slate.id

    before = _snapshot_count(engine)

    features = await client.get(f"/api/predictions/slates/{slate_id}/features")
    assert features.status_code == 200
    assert _snapshot_count(engine) == before, "GET /features wrote a snapshot"

    quality = await client.get(f"/api/predictions/slates/{slate_id}/quality")
    assert quality.status_code == 200
    assert _snapshot_count(engine) == before, "GET /quality wrote a snapshot"

    ticket = await client.get(f"/api/predictions/slates/{slate_id}/ticket")
    assert ticket.status_code == 200
    assert _snapshot_count(engine) == before, "GET /ticket wrote a snapshot"

    # Hitting them a second time (cache is read-only, so still a miss) must
    # also stay flat.
    await client.get(f"/api/predictions/slates/{slate_id}/features")
    await client.get(f"/api/predictions/slates/{slate_id}/quality")
    await client.get(f"/api/predictions/slates/{slate_id}/ticket")
    assert _snapshot_count(engine) == before
