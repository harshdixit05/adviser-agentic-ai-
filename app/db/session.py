from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commits on success, rolls back on any exception."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
