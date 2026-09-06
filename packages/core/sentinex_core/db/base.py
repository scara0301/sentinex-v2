from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional


class Base(DeclarativeBase):
    pass


_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine(database_url: str) -> None:
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    # Calling this before init_engine() previously raised
    # "TypeError: 'NoneType' object is not callable", which says nothing
    # about the actual mistake.
    if _sessionmaker is None:
        raise RuntimeError(
            "Database engine is not initialized; call init_engine(database_url) "
            "during application startup before opening a session."
        )
    async with _sessionmaker() as session:
        yield session
