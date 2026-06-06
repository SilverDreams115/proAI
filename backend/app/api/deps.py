from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.db import session as db_session


async def get_db_session() -> AsyncIterator[Session]:
    session = db_session.SessionLocal()
    try:
        yield session
    finally:
        session.close()
