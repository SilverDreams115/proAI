"""Short in-process cache for read-only diagnostic reports.

The diagnostic tab can request several expensive, overlapping reports at once.
These payloads are read-only and operator-facing, so a very short TTL keeps the
UI responsive without hiding meaningful live changes. Callers still own the
returned object: values are deep-copied on read/write.
"""
from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any, Hashable

DEFAULT_DIAGNOSTIC_TTL_SECONDS = 15.0

_cache_guard = threading.Lock()
_cache: dict[tuple[str, Hashable], tuple[float, Any]] = {}
_build_locks: dict[tuple[str, Hashable], threading.Lock] = {}


def get_diagnostic_cache(namespace: str, key: Hashable) -> Any | None:
    now = time.monotonic()
    cache_key = (namespace, key)
    with _cache_guard:
        entry = _cache.get(cache_key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= now:
            _cache.pop(cache_key, None)
            return None
        return copy.deepcopy(value)


def set_diagnostic_cache(
    namespace: str,
    key: Hashable,
    value: Any,
    *,
    ttl_seconds: float = DEFAULT_DIAGNOSTIC_TTL_SECONDS,
) -> None:
    cache_key = (namespace, key)
    with _cache_guard:
        _cache[cache_key] = (time.monotonic() + ttl_seconds, copy.deepcopy(value))


def cached_diagnostic_report(
    namespace: str,
    key: Hashable,
    builder: Callable[[], Any],
    *,
    ttl_seconds: float = DEFAULT_DIAGNOSTIC_TTL_SECONDS,
) -> Any:
    cached = get_diagnostic_cache(namespace, key)
    if cached is not None:
        return cached

    cache_key = (namespace, key)
    with _cache_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[cache_key] = lock

    with lock:
        cached = get_diagnostic_cache(namespace, key)
        if cached is not None:
            return cached
        value = builder()
        set_diagnostic_cache(namespace, key, value, ttl_seconds=ttl_seconds)
        return copy.deepcopy(value)


def clear_diagnostic_cache(namespace: str | None = None) -> None:
    with _cache_guard:
        if namespace is None:
            _cache.clear()
            _build_locks.clear()
            return
        for key in [key for key in _cache if key[0] == namespace]:
            _cache.pop(key, None)
        for key in [key for key in _build_locks if key[0] == namespace]:
            _build_locks.pop(key, None)
