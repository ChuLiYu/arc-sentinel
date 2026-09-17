"""SQLAlchemy engine and session factory.

The engine is created lazily (``get_engine()``) so import-time side effects
(e.g. connecting to the DB) never happen for pure unit tests that only touch
``decode.py`` or the ORM metadata.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return a bound sessionmaker configured against :func:`get_engine`."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a transactional session, always closing it."""
    factory = get_session_factory()
    with factory() as session:
        yield session


def dispose_engine() -> None:
    """Dispose the engine and reset cached singletons (used by tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
