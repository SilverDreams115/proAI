from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar


T = TypeVar("T")


class SlatePredictionCache(Generic[T]):
    """Short-lived per-slate cache with separate read-only/persisting buckets."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._persisting: dict[str, tuple[float, T]] = {}
        self._readonly: dict[str, tuple[float, T]] = {}
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, bool], threading.Lock] = {}

    def get(self, slate_id: str, *, persist_audit: bool) -> T | None:
        cache = self._persisting if persist_audit else self._readonly
        entry = cache.get(slate_id)
        if entry is None:
            return None
        cached_at, value = entry
        if time.monotonic() - cached_at > self.ttl_seconds:
            cache.pop(slate_id, None)
            return None
        return value

    def set(self, slate_id: str, value: T, *, persist_audit: bool) -> None:
        cache = self._persisting if persist_audit else self._readonly
        cache[slate_id] = (time.monotonic(), value)

    def lock_for(self, slate_id: str, *, persist_audit: bool) -> threading.Lock:
        key = (slate_id, persist_audit)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def invalidate(self, slate_id: str | None = None) -> None:
        if slate_id is None:
            self._persisting.clear()
            self._readonly.clear()
            return
        self._persisting.pop(slate_id, None)
        self._readonly.pop(slate_id, None)
