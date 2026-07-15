from __future__ import annotations

from app.services.prediction_cache import SlatePredictionCache


def test_slate_prediction_cache_keeps_readonly_and_persisting_buckets_separate() -> None:
    cache: SlatePredictionCache[list[str]] = SlatePredictionCache(ttl_seconds=30)

    cache.set("s1", ["readonly"], persist_audit=False)
    cache.set("s1", ["persisting"], persist_audit=True)

    assert cache.get("s1", persist_audit=False) == ["readonly"]
    assert cache.get("s1", persist_audit=True) == ["persisting"]

    cache.invalidate("s1")

    assert cache.get("s1", persist_audit=False) is None
    assert cache.get("s1", persist_audit=True) is None
