"""Database engine, session factory, and FastAPI dependency.

The SQLAlchemy `Base` (DeclarativeBase) lives in `app.models.base` per
`docs/REPO_LAYOUT.md`; this module owns connection lifecycle only.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _make_engine(url: str) -> Engine:
    """Construct the SQLAlchemy engine.

    `pool_pre_ping=True` is non-negotiable for cloud Postgres: idle
    connections through NAT can be silently reset, and we'd rather pay one
    extra round-trip than serve a 500.
    """
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
    )


_settings = get_settings()
engine: Engine = _make_engine(_settings.DATABASE_URL)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, ensures cleanup on completion.

    Note: this returns an *unscoped* session. Tenant scoping is applied by
    `get_scoped_session` (see P0.10 / `docs/TENANCY.md`); plain `get_db` is
    for endpoints that legitimately operate cross-tenant (auth, connector
    enrollment, system health).
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
