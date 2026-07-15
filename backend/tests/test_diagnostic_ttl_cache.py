from __future__ import annotations

import threading
import time

from app.services.diagnostic_ttl_cache import (
    cached_diagnostic_report,
    clear_diagnostic_cache,
)


def test_diagnostic_cache_returns_independent_copies():
    clear_diagnostic_cache()
    calls = 0

    def builder():
        nonlocal calls
        calls += 1
        return {"items": [1]}

    first = cached_diagnostic_report("test", "copy", builder, ttl_seconds=5)
    first["items"].append(2)
    second = cached_diagnostic_report("test", "copy", builder, ttl_seconds=5)

    assert calls == 1
    assert second == {"items": [1]}


def test_diagnostic_cache_expires():
    clear_diagnostic_cache()
    calls = 0

    def builder():
        nonlocal calls
        calls += 1
        return {"calls": calls}

    assert cached_diagnostic_report("test", "ttl", builder, ttl_seconds=0.01)["calls"] == 1
    time.sleep(0.02)
    assert cached_diagnostic_report("test", "ttl", builder, ttl_seconds=0.01)["calls"] == 2


def test_diagnostic_cache_single_flights_concurrent_builds():
    clear_diagnostic_cache()
    calls = 0
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def builder():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.03)
        return {"ok": True}

    errors: list[BaseException] = []
    results: list[dict[str, bool]] = []

    def worker():
        try:
            barrier.wait(timeout=1)
            results.append(
                cached_diagnostic_report("test", "single-flight", builder, ttl_seconds=5)
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert results == [{"ok": True}] * 8
    assert calls == 1
