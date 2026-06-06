from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import close_all_sessions
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings


def _build_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine_kwargs = {"future": True, "connect_args": connect_args}
    if not database_url.startswith("sqlite"):
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 1800
    return create_engine(database_url, **engine_kwargs)


engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def configure_session(database_url: str) -> None:
    global engine
    global SessionLocal

    settings.database_url = database_url
    close_all_sessions()
    engine.dispose()
    engine = _build_engine(database_url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def managed_transaction(session: Session) -> Iterator[Session]:
    depth = int(session.info.get("managed_transaction_depth", 0))
    session.info["managed_transaction_depth"] = depth + 1
    try:
        yield session
        if depth == 0:
            session.commit()
    except Exception:
        if depth == 0:
            session.rollback()
        raise
    finally:
        session.info["managed_transaction_depth"] = depth
