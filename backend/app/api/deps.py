from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import verify_session_token
from app.core.settings import settings
from app.db import session as db_session


async def get_db_session() -> AsyncIterator[Session]:
    session = db_session.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_worker_auth(request: Request) -> None:
    """Per-route auth guard for worker endpoints.

    Enforces API-key or session auth whenever credentials are configured,
    regardless of the global auth_required flag. This prevents worker routes
    from being reachable without auth on staging/dev environments that have
    credentials set but auth_required=False.

    Falls through silently when no credentials are configured at all — that
    matches the all-routes-open bare-dev posture intentionally.
    """
    has_credentials = bool(settings.auth_api_key or settings.session_secret)
    if not has_credentials:
        return

    provided_key = request.headers.get("X-API-Key")
    if not provided_key:
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            provided_key = token

    api_key_valid = (
        provided_key is not None
        and settings.auth_api_key is not None
        and secrets.compare_digest(provided_key, settings.auth_api_key)
    )
    session_valid = verify_session_token(
        request.cookies.get(settings.auth_session_cookie_name),
        secret=settings.session_secret,
    )

    if not api_key_valid and not session_valid:
        raise HTTPException(status_code=401, detail="Authentication required.")
