from __future__ import annotations

from time import time

from fastapi import APIRouter
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel

from app.core.auth import create_session_token
from app.core.auth import verify_session_token
from app.core.auth import verify_password
from app.core.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])
LOGIN_FAILURE_WINDOW_SECONDS = 300
LOGIN_FAILURE_LIMIT = 5
# In-process store. Safe only with `uvicorn --workers 1` (enforced in
# Dockerfile). When moving to a multi-worker setup, replace with a shared
# store (Postgres table or Redis).
_login_failures: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    authenticated: bool
    method: str | None = None


@router.post("/login", response_model=SessionResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> SessionResponse:
    client_key = _login_client_key(request)
    if _is_login_throttled(client_key):
        response.status_code = 429
        return SessionResponse(authenticated=False)
    if not verify_password(payload.password, settings.auth_password_hash):
        _record_login_failure(client_key)
        response.status_code = 401
        return SessionResponse(authenticated=False)
    if not settings.session_secret:
        response.status_code = 503
        return SessionResponse(authenticated=False)
    _clear_login_failures(client_key)

    token = create_session_token(
        secret=settings.session_secret,
        max_age_seconds=settings.auth_session_ttl_seconds,
    )
    response.set_cookie(
        settings.auth_session_cookie_name,
        token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.force_https,
        samesite="lax",
        path="/",
    )
    return SessionResponse(authenticated=True, method="password")


@router.post("/logout", response_model=SessionResponse)
async def logout(response: Response) -> SessionResponse:
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path="/",
        secure=settings.force_https,
        httponly=True,
        samesite="lax",
    )
    return SessionResponse(authenticated=False)


@router.get("/session", response_model=SessionResponse)
async def session(request: Request) -> SessionResponse:
    token = request.cookies.get(settings.auth_session_cookie_name)
    authenticated = verify_session_token(token, secret=settings.session_secret)
    return SessionResponse(authenticated=authenticated, method="session" if authenticated else None)


def _login_client_key(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_login_throttled(client_key: str) -> bool:
    cutoff = time() - LOGIN_FAILURE_WINDOW_SECONDS
    failures = [timestamp for timestamp in _login_failures.get(client_key, []) if timestamp >= cutoff]
    _login_failures[client_key] = failures
    return len(failures) >= LOGIN_FAILURE_LIMIT


def _record_login_failure(client_key: str) -> None:
    failures = _login_failures.setdefault(client_key, [])
    failures.append(time())


def _clear_login_failures(client_key: str) -> None:
    _login_failures.pop(client_key, None)
