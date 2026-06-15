"""Async engine, PRAGMA wiring, session factory, schema create/heal, StorageHandle.

One engine per university DB. PRAGMAs (WAL, busy_timeout, NORMAL, foreign_keys)
are set on every new DBAPI connection. JSON columns serialize with
ensure_ascii=False so Chinese text stays readable in the stored TEXT (overview §6).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from dext.storage.models import Base

if TYPE_CHECKING:  # avoid importing the writer at module load (no cycle)
    from dext.storage.writer import DBWriter

# SQLite type fallbacks for ensure_columns ALTER TABLE (KISS: nullable adds only).
_SQLITE_AFFINITY = {"INTEGER": "INTEGER", "FLOAT": "REAL", "TEXT": "TEXT", "JSON": "TEXT", "VARCHAR": "TEXT"}


class StorageError(Exception):
    """A storage-lifecycle problem (bad path, missing DB on resume, etc.)."""


def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def create_engine_for_path(db_path) -> AsyncEngine:
    """Create an async engine for a SQLite file path, with PRAGMAs + UTF-8 JSON."""
    if not str(db_path):
        raise StorageError("create_engine_for_path requires a non-empty path")
    abs_posix = Path(db_path).resolve().as_posix()  # cross-platform sqlite URL
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{abs_posix}",
        json_serializer=_json_dumps,
        json_deserializer=json.loads,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    # expire_on_commit=False: returned ids/attrs stay usable after commit.
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Idempotently create every table (no-op for tables that already exist)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_columns(engine: AsyncEngine) -> None:
    """Lightweight forward-migration: ADD COLUMN for any column missing from an
    existing table (resume path, spec §7). Nullable adds only — no Alembic."""
    async with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            info = (await conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')")).all()
            if not info:
                continue  # table absent entirely → create_all handles it
            existing = {row[1] for row in info}
            for col in table.columns:
                if col.name in existing:
                    continue
                affinity = _SQLITE_AFFINITY.get(col.type.__class__.__name__.upper(), "TEXT")
                await conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {affinity}'
                )


class StorageHandle:
    """Owns one university DB: the engine, the single-writer DBWriter (running as
    a task), and a read-session factory. close() drains the writer then disposes."""

    def __init__(
        self,
        engine: AsyncEngine,
        writer: "DBWriter",
        session_factory,
        *,
        university_id=None,
        backup_path: Path | None = None,
    ):
        self.engine = engine
        self.writer = writer
        self.session_factory = session_factory
        self.university_id = university_id
        self.backup_path = backup_path
        self._writer_task = None

    def start_writer(self) -> None:
        import asyncio

        self._writer_task = asyncio.create_task(self.writer.run())

    @asynccontextmanager
    async def session(self):
        """A fresh read/ad-hoc AsyncSession (WAL allows concurrent reads)."""
        async with self.session_factory() as s:
            yield s

    async def close(self) -> None:
        await self.writer.stop()
        if self._writer_task is not None:
            await self._writer_task
        await self.engine.dispose()


async def healthcheck(engine: AsyncEngine) -> bool:
    """True if the DB answers a trivial query (used by lifecycle)."""
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT 1"))).scalar() == 1
