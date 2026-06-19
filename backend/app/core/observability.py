"""Sentry SDK bootstrap with an env-gated initializer.

The whole module is import-time silent unless `PROAI_SENTRY_DSN` is
configured. With no DSN the call to `init_sentry()` returns immediately;
with a DSN we attempt to import sentry-sdk and wire it to the FastAPI
+ logging integrations. If sentry-sdk is missing we log a warning and
continue — Sentry is opt-in observability, never a hard dependency.

Why the indirection: keeping the Sentry import inside the function body
means the production image can ship without sentry-sdk installed when
the operator hasn't asked for it. The dependency only becomes mandatory
once a DSN appears in the environment.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("proai.observability")


def init_sentry(
    *,
    dsn: str | None,
    environment: str,
    release: str | None,
    traces_sample_rate: float = 0.0,
    profiles_sample_rate: float = 0.0,
) -> bool:
    """Initialize Sentry if a DSN is configured.

    Returns True on a successful init, False otherwise (no DSN, missing
    SDK, or sentry rejected the config). Callers do not need to branch on
    the return value — it's surfaced so the startup log can report
    whether Sentry is live."""
    if not dsn:
        return False
    try:
        # sentry-sdk is an optional extra (see the ImportError branch). It
        # isn't a declared dependency, so a root-level mypy run that misses
        # backend/pyproject.toml's ignore_missing_imports flags it as
        # import-not-found; the runtime guard below already handles its
        # absence, so the missing stub is safe to ignore here.
        import sentry_sdk  # type: ignore[import-not-found]
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore[import-not-found]
        from sentry_sdk.integrations.logging import LoggingIntegration  # type: ignore[import-not-found]
        from sentry_sdk.integrations.starlette import StarletteIntegration  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "PROAI_SENTRY_DSN is set but sentry-sdk is not installed; "
            "skipping init. Add 'sentry-sdk[fastapi]' to backend/pyproject.toml "
            "if you want crash reporting enabled."
        )
        return False

    integrations: list[Any] = [
        StarletteIntegration(transaction_style="endpoint"),
        FastApiIntegration(transaction_style="endpoint"),
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
    ]
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=integrations,
            send_default_pii=False,
        )
    except Exception as exc:  # noqa: BLE001 — Sentry init must never crash boot
        logger.warning("sentry init failed: %s", exc)
        return False
    logger.info(
        "sentry initialized (env=%s, release=%s, traces=%.2f)",
        environment,
        release or "unset",
        traces_sample_rate,
    )
    return True
