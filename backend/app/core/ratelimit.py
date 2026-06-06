"""Global IP-based rate limiter.

Sits in front of every authenticated /api route as a sliding-window
counter — same pattern used by `auth._is_login_throttled` but scoped
to the whole API surface, not just login. In-memory only: a single
worker today, so a shared store (redis, memcache) would be premature.

Settings drive the limits so the deployment can dial them per
environment without code changes. Disabled by default in non-prod
(the limit field is None, which short-circuits the check) so dev
loops don't hit the throttle while iterating.
"""

from __future__ import annotations

from collections import defaultdict, deque
from time import time
from typing import Deque

# Per-IP timestamps of recent requests. Deques are bounded by the
# active window, not the limit, so cleanup amortises into the read
# path; no background reaper needed.
_request_history: dict[str, Deque[float]] = defaultdict(deque)


def reset_rate_limit_state() -> None:
    """Test hook: drop the in-memory history between tests."""
    _request_history.clear()


def is_rate_limited(
    client_key: str,
    *,
    window_seconds: float,
    max_requests: int,
) -> bool:
    """Return True when the caller would exceed `max_requests` in the
    trailing `window_seconds`. The current call counts toward the
    limit only when it is allowed — callers should record it via
    `record_request` after the limit check passes."""
    if max_requests <= 0 or window_seconds <= 0:
        return False
    history = _request_history[client_key]
    cutoff = time() - window_seconds
    while history and history[0] < cutoff:
        history.popleft()
    return len(history) >= max_requests


def record_request(client_key: str) -> None:
    """Stamp the current call on the caller's history. Idempotent for
    the test hook — call after the limit check confirms the request
    is allowed."""
    _request_history[client_key].append(time())
