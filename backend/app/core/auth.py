from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
DEFAULT_PASSWORD_ITERATIONS = 390000


def hash_password(password: str, *, salt: str | None = None, iterations: int = DEFAULT_PASSWORD_ITERATIONS) -> str:
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        iterations,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${iterations}${salt_value}${digest}"


def verify_password(password: str, password_hash: str | None) -> bool:
    parsed = _parse_password_hash(password_hash)
    if parsed is None:
        return False
    iterations, salt, expected_digest = parsed
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(actual_digest, expected_digest)


def is_valid_password_hash(password_hash: str | None) -> bool:
    return _parse_password_hash(password_hash) is not None


def create_session_token(*, secret: str, max_age_seconds: int, subject: str = "admin") -> str:
    payload = {
        "sub": subject,
        "exp": int(time.time()) + max_age_seconds,
        "nonce": secrets.token_urlsafe(18),
    }
    encoded_payload = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(encoded_payload, secret)
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str | None, *, secret: str | None) -> bool:
    if not token or not secret:
        return False
    payload_part, separator, signature = token.partition(".")
    if not separator:
        return False
    expected_signature = _sign(payload_part, secret)
    if not secrets.compare_digest(signature, expected_signature):
        return False
    try:
        payload = json.loads(_urlsafe_b64decode(payload_part).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int):
        return False
    return expires_at > int(time.time())


def _parse_password_hash(password_hash: str | None) -> tuple[int, str, str] | None:
    if not password_hash:
        return None
    parts = password_hash.split("$")
    if len(parts) != 4:
        return None
    algorithm, iterations_raw, salt, digest = parts
    if algorithm != PASSWORD_HASH_ALGORITHM:
        return None
    try:
        iterations = int(iterations_raw)
    except ValueError:
        return None
    if iterations < 100000 or len(salt) < 16 or len(digest) < 64:
        return None
    return iterations, salt, digest


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return _urlsafe_b64encode(digest)


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
