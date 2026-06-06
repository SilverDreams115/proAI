from __future__ import annotations

from typing import TypedDict

from sqlalchemy import text

from app.db import session as db_session
from app.db.migrations import SCHEMA_VERSION
from app.core.settings import redact_url_secret


class DatabaseHealth(TypedDict, total=False):
    database_ok: bool
    configured_database_url: str
    schema_version: int
    schema_up_to_date: bool
    detail: str


def get_database_health() -> DatabaseHealth:
    try:
        with db_session.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = connection.execute(text("SELECT version FROM schema_migrations LIMIT 1")).scalar_one_or_none()
    except Exception as exc:
        return {
            "database_ok": False,
            "configured_database_url": redact_url_secret(db_session.settings.database_url),
            "schema_version": 0,
            "schema_up_to_date": False,
            "detail": str(exc),
        }
    current_version = int(current or 0)
    return {
        "database_ok": True,
        "configured_database_url": redact_url_secret(db_session.settings.database_url),
        "schema_version": current_version,
        "schema_up_to_date": current_version >= SCHEMA_VERSION,
    }
