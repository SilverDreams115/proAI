"""Tests for the feature snapshot cache (Fase 2.3)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.feature_service import FeatureService


class _Snapshot:
    def __init__(self, *, generated_at: datetime, payload: dict, version: str) -> None:
        self.generated_at = generated_at
        self.payload_json = json.dumps(payload)
        self.feature_set_version = version


class _FakeSession:
    """Tracks scalar() calls so the test can verify the cache short-circuits
    the recompute path."""

    def __init__(self, snapshot: _Snapshot | None) -> None:
        self._snapshot = snapshot
        self.scalar_calls = 0
        self.info: dict = {}

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self._snapshot

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakeRepo:
    def __init__(self, snapshot: _Snapshot | None, match) -> None:
        self.session = _FakeSession(snapshot)
        self._match = match
        self.list_match_evidence_calls = 0

    def get_match(self, _match_id):
        return self._match

    def list_match_evidence(self, _match_id):
        self.list_match_evidence_calls += 1
        return []

    def list_match_availability(self, _match_id):
        return []

    def count_evidence_items(self, _match_id):
        return 0

    def count_linked_documents(self, _match_id):
        return 0

    def save_snapshot(self, _match_id, _version, payload):
        # Simulate persistence: snapshot has the same shape as a model row.
        return SimpleNamespace(generated_at=datetime.now(timezone.utc), payload_json=json.dumps(payload))


@pytest.fixture
def fake_match() -> SimpleNamespace:
    return SimpleNamespace(
        id="match-cache-1",
        venue="Stadium",
        kickoff_at=datetime.now(timezone.utc) + timedelta(days=1),
        home_team=SimpleNamespace(name="Club A", country="MX"),
        away_team=SimpleNamespace(name="Club B", country="MX"),
        home_team_id="t-a",
        away_team_id="t-b",
    )


def test_cache_returns_fresh_snapshot_without_recomputing(fake_match) -> None:
    """A fresh snapshot (within TTL) must short-circuit the heavy
    recomputation path -- repository helpers like list_match_evidence
    should not be called when the cache is hit."""
    cached_payload = {"hours_to_kickoff": 12.0, "venue_known": True, "evidence_items": 0}
    snapshot = _Snapshot(
        generated_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        payload=cached_payload,
        version=FeatureService.FEATURE_SET_VERSION,
    )
    repo = _FakeRepo(snapshot, fake_match)
    service = FeatureService(repo)

    _, payload, _generated_at = service.build_match_features(fake_match.id)

    assert payload == cached_payload
    assert repo.list_match_evidence_calls == 0, (
        "cache hit should skip the heavy recompute path"
    )


def test_cache_rebuilds_when_snapshot_is_too_old(fake_match) -> None:
    """A snapshot older than the TTL must be discarded and a fresh one
    rebuilt from the repository helpers."""
    stale = _Snapshot(
        generated_at=datetime.now(timezone.utc)
        - timedelta(seconds=FeatureService.SNAPSHOT_TTL_SECONDS + 10),
        payload={"hours_to_kickoff": 999.0},
        version=FeatureService.FEATURE_SET_VERSION,
    )
    repo = _FakeRepo(stale, fake_match)
    service = FeatureService(repo)

    _, payload, _ = service.build_match_features(fake_match.id)

    # The cache returned None -> the slow path ran and called the repo.
    assert repo.list_match_evidence_calls == 1
    # The stale `999.0` cached value did not leak through.
    assert payload["hours_to_kickoff"] != 999.0


def test_cache_skipped_when_use_cache_false(fake_match) -> None:
    """The caller can force a recompute by passing use_cache=False."""
    fresh = _Snapshot(
        generated_at=datetime.now(timezone.utc),
        payload={"hours_to_kickoff": 1.0},
        version=FeatureService.FEATURE_SET_VERSION,
    )
    repo = _FakeRepo(fresh, fake_match)
    service = FeatureService(repo)

    service.build_match_features(fake_match.id, use_cache=False)

    assert repo.list_match_evidence_calls == 1
