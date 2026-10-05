"""Database base, session and the unit-of-work transaction boundary."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from ..core.config import Settings, get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, server_default="now()"
    )


_engine = None
_Session: sessionmaker | None = None


def get_engine(settings: Settings | None = None):
    global _engine
    if _engine is None:
        s = settings or get_settings()
        _engine = create_engine(
            s.database_url,
            echo=s.db_echo,
            pool_pre_ping=True,
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
        )
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker:
    global _Session
    if _Session is None:
        _Session = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False
        )
    return _Session


def get_db() -> Iterator[Any]:
    """FastAPI dependency — one session per request, rolled back on error."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()