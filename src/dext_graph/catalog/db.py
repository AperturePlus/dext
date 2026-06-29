"""SQLite lifecycle and the single catalog writer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from dext_graph.catalog.models import CATALOG_SCHEMA_VERSION, SCHEMA_SQL

T = TypeVar("T")


class CatalogError(RuntimeError):
    """A user-actionable catalog or graph-build failure."""


@contextmanager
def catalog_write_lock(catalog_path: str | Path):
    """Hold a non-blocking cross-process lock for one mutating catalog command."""
    path = Path(catalog_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".writer.lock")
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise CatalogError(
            f"another process is already writing catalog: {path}"
        ) from exc
    try:
        yield lock_path
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _configure(connection: sqlite3.Connection, *, query_only: bool = False) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    if query_only:
        connection.execute("PRAGMA query_only=ON")
    else:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")


def connect_catalog(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path), isolation_level=None)
    _configure(connection)
    return connection


def connect_catalog_read_only(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise CatalogError(f"catalog does not exist: {resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    _configure(connection, query_only=True)
    return connection


def initialize_catalog(path: str | Path) -> Path:
    catalog_path = Path(path).expanduser().resolve()
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_catalog(catalog_path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if existing:
                raise CatalogError(
                    "existing catalog has no recognized schema version; refusing to modify it"
                )
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
                (str(CATALOG_SCHEMA_VERSION),),
            )
            connection.execute(f"PRAGMA user_version={CATALOG_SCHEMA_VERSION}")
        elif version == CATALOG_SCHEMA_VERSION:
            connection.executescript(SCHEMA_SQL)
            recorded = connection.execute(
                "SELECT value FROM catalog_meta WHERE key='schema_version'"
            ).fetchone()
            if recorded is None or int(recorded[0]) != CATALOG_SCHEMA_VERSION:
                raise CatalogError("catalog schema metadata disagrees with PRAGMA user_version")
        else:
            raise CatalogError(
                f"catalog schema version {version} is incompatible with supported version "
                f"{CATALOG_SCHEMA_VERSION}"
            )
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise CatalogError(f"catalog quick_check failed: {result}")
    finally:
        connection.close()
    return catalog_path


def online_backup(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    pages: int = 256,
    progress_hook: Callable[[int, int, int], None] | None = None,
) -> None:
    """Create one WAL-consistent backup without writing to the source database."""
    source = Path(source_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.execute("PRAGMA query_only=ON")
        src.execute("PRAGMA busy_timeout=15000")

        def progress(status: int, remaining: int, total: int) -> None:
            if progress_hook is not None:
                progress_hook(status, remaining, total)
            time.sleep(0)

        src.backup(dst, pages=pages, progress=progress, sleep=0.05)
        dst.commit()
    finally:
        dst.close()
        src.close()


def verify_sqlite(path: str | Path) -> None:
    connection = sqlite3.connect(Path(path))
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise CatalogError(f"SQLite quick_check failed for {path}: {result}")
    finally:
        connection.close()


def backup_existing_catalog(
    catalog_path: str | Path,
    *,
    progress_hook: Callable[[int, int, int], None] | None = None,
) -> Path | None:
    path = Path(catalog_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = path.parent / "backups" / f"catalog-{stamp}.db"
    online_backup(path, destination, progress_hook=progress_hook)
    verify_sqlite(destination)
    digest = file_sha256(destination)
    if digest == hashlib.sha256(b"").hexdigest():
        raise CatalogError("catalog backup unexpectedly produced an empty file")
    if file_sha256(destination) != digest:
        raise CatalogError("catalog backup changed during hash readback")
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        digest + "\n", encoding="ascii"
    )
    return destination


def snapshot_protection_reasons(
    catalog_path: str | Path, source_snapshot_id: str
) -> list[dict[str, str]]:
    """Return the stage-1 protection set that a future snapshot GC must honor."""
    reasons: list[dict[str, str]] = []
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        for row in connection.execute(
            """
            SELECT b.id, b.status FROM build_source_snapshots bs
            JOIN graph_builds b ON b.id=bs.build_id
            WHERE bs.source_snapshot_id=? AND b.status IN ('READY','ACTIVE')
            ORDER BY b.id
            """,
            (source_snapshot_id,),
        ):
            reasons.append(
                {"kind": f"build:{row['status'].lower()}", "reference_id": row["id"]}
            )
        for row in connection.execute(
            """
            SELECT p.protection_kind, p.reference_id, q.resolved
            FROM source_snapshot_protections p
            LEFT JOIN quality_findings q
              ON p.protection_kind='unresolved_finding' AND q.id=p.reference_id
            WHERE p.source_snapshot_id=?
            ORDER BY p.protection_kind, p.reference_id
            """,
            (source_snapshot_id,),
        ):
            if row["protection_kind"] == "unresolved_finding" and row["resolved"] != 0:
                continue
            reasons.append(
                {"kind": row["protection_kind"], "reference_id": row["reference_id"]}
            )
        for row in connection.execute(
            """
            SELECT DISTINCT q.id FROM quality_findings q
            JOIN professor_observations o ON o.id=q.observation_id
            WHERE o.source_snapshot_id=? AND q.resolved=0
            ORDER BY q.id
            """,
            (source_snapshot_id,),
        ):
            candidate = {"kind": "unresolved_finding", "reference_id": row["id"]}
            if candidate not in reasons:
                reasons.append(candidate)
    return reasons


@dataclass
class _WriteCommand:
    action: Callable[[sqlite3.Connection], Any] | None
    future: asyncio.Future[Any]
    transactional: bool = True


class CatalogWriter:
    """One task owns the only writable catalog connection and a bounded queue."""

    def __init__(self, path: str | Path, *, max_queue: int = 2):
        if max_queue <= 0:
            raise ValueError("max_queue must be positive")
        self.path = Path(path).expanduser().resolve()
        self.queue: asyncio.Queue[_WriteCommand] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "CatalogWriter":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()

    async def execute(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        transactional: bool = True,
    ) -> T:
        if self._task is None:
            raise RuntimeError("CatalogWriter has not been started")
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        await self.queue.put(_WriteCommand(action, future, transactional))
        return await future

    async def close(self) -> None:
        if self._task is None:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self.queue.put(_WriteCommand(None, future, False))
        await future
        await self._task
        self._task = None

    async def _run(self) -> None:
        connection = connect_catalog(self.path)
        try:
            while True:
                command = await self.queue.get()
                try:
                    if command.action is None:
                        if not command.future.done():
                            command.future.set_result(None)
                        return
                    if command.transactional:
                        connection.execute("BEGIN IMMEDIATE")
                    try:
                        result = command.action(connection)
                    except BaseException as exc:
                        if command.transactional and connection.in_transaction:
                            connection.rollback()
                        if not command.future.done():
                            command.future.set_exception(exc)
                    else:
                        if command.transactional and connection.in_transaction:
                            connection.commit()
                        if not command.future.done():
                            command.future.set_result(result)
                finally:
                    self.queue.task_done()
        finally:
            connection.close()


__all__ = [
    "CatalogError",
    "CatalogWriter",
    "backup_existing_catalog",
    "catalog_write_lock",
    "connect_catalog_read_only",
    "file_sha256",
    "initialize_catalog",
    "json_dumps",
    "json_loads",
    "online_backup",
    "snapshot_protection_reasons",
    "utcnow_iso",
    "verify_sqlite",
]
