"""Async database setup for recommendation application state."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dext_recommend.app_state.models import Base, REQUIRED_TABLES


class SchemaNotReadyError(RuntimeError):
    """Raised when app-state tables are missing and bootstrap is disabled."""


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def create_async_engine_from_settings(settings: Any) -> AsyncEngine:
    url = normalize_database_url(settings.database_url)
    kwargs: dict[str, Any] = {}
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_pre_ping=True,
            pool_timeout=settings.database_pool_timeout,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
    return create_async_engine(url, **kwargs)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ensure_schema_ready(engine: AsyncEngine, *, bootstrap: bool = False) -> None:
    async with engine.begin() as conn:
        if bootstrap:
            await conn.run_sync(Base.metadata.create_all)
        missing = await conn.run_sync(_missing_tables)
        if missing:
            raise SchemaNotReadyError(
                "recommendation app-state schema is missing tables: "
                + ", ".join(sorted(missing))
                + "; enable dev schema bootstrap or run the migration follow-up",
            )
        await conn.execute(text("SELECT 1"))


def _missing_tables(sync_conn) -> set[str]:
    existing = set(inspect(sync_conn).get_table_names())
    return set(REQUIRED_TABLES) - existing


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        async with session.begin():
            yield session


__all__ = [
    "SchemaNotReadyError",
    "create_async_engine_from_settings",
    "create_sessionmaker",
    "ensure_schema_ready",
    "normalize_database_url",
    "session_scope",
]
