"""Sanity coverage for the in-memory rate limiter (P11).

Keeps the test surface narrow: we don't try to exercise the FastAPI
middleware end-to-end here — that's covered by the smoke run after
deploy — just the pure sliding-window helper so an off-by-one in
the cutoff math doesn't slip through silently.
"""

from __future__ import annotations

import time

from app.core.ratelimit import (
    is_rate_limited,
    record_request,
    reset_rate_limit_state,
)


def test_allows_calls_below_limit() -> None:
    reset_rate_limit_state()
    for _ in range(3):
        assert not is_rate_limited("alpha", window_seconds=60, max_requests=5)
        record_request("alpha")


def test_blocks_when_limit_exceeded() -> None:
    reset_rate_limit_state()
    for _ in range(5):
        assert not is_rate_limited("beta", window_seconds=60, max_requests=5)
        record_request("beta")
    # 6th call exceeds the limit
    assert is_rate_limited("beta", window_seconds=60, max_requests=5)


def test_per_client_isolation() -> None:
    reset_rate_limit_state()
    for _ in range(5):
        record_request("gamma")
    assert is_rate_limited("gamma", window_seconds=60, max_requests=5)
    # Another client must not be affected by gamma's history.
    assert not is_rate_limited("delta", window_seconds=60, max_requests=5)


def test_window_expiry_evicts_old_calls() -> None:
    reset_rate_limit_state()
    for _ in range(5):
        record_request("epsilon")
    # Simulate the window having slid past — wind the dq entries
    # back so they fall outside the window without sleeping.
    from app.core.ratelimit import _request_history

    history = _request_history["epsilon"]
    now = time.time()
    for idx in range(len(history)):
        history[idx] = now - 120  # 2 minutes ago, outside a 60s window
    assert not is_rate_limited("epsilon", window_seconds=60, max_requests=5)


def test_disabled_when_max_requests_zero() -> None:
    reset_rate_limit_state()
    for _ in range(50):
        record_request("zeta")
    # max_requests=0 short-circuits and lets every call through.
    assert not is_rate_limited("zeta", window_seconds=60, max_requests=0)
