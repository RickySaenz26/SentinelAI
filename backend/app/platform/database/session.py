"""Synchronous SQLAlchemy session lifecycle with transaction-local tenant context."""

from collections.abc import Generator
from threading import Lock
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None
_engine_lock = Lock()


def get_engine() -> Engine:
    """Return one process-local engine; tests may explicitly dispose and replace it."""
    global _engine, _engine_url, _session_factory
    database_url = get_settings().database_url_value
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    with _engine_lock:
        if _engine is None or _engine_url != database_url:
            if _engine is not None:
                _engine.dispose()
            _engine = create_engine(
                database_url, pool_pre_ping=True, pool_reset_on_return="rollback"
            )
            _engine_url = database_url
            _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    if _session_factory is None:
        raise RuntimeError("Session factory was not initialized.")
    return _session_factory


def dispose_engine_for_tests() -> None:
    global _engine, _engine_url, _session_factory
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _session_factory = None


def set_organization_context(session: Session, organization_id: UUID) -> None:
    """Set PostgreSQL RLS context for the current transaction only."""
    session.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )
    session.execute(text("SELECT set_config('app.user_id', '', true)"))


def set_user_context(session: Session, user_id: UUID) -> None:
    """Set only the authenticated user identity while the active tenant is being resolved."""
    session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


def get_db_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
