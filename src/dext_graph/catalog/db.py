"""SQLite lifecycle and the single catalog writer."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from dext_graph.catalog.models import (
    CATALOG_SCHEMA_VERSION,
    CURATION_SCHEMA_SQL,
    EVIDENCE_GRAPH_SCHEMA_SQL,
    RELEASE_SCHEMA_SQL,
    SEMANTIC_VECTOR_SCHEMA_SQL,
    SCHEMA_SQL,
    TOPIC_SCHEMA_SQL,
)

T = TypeVar("T")

_BACKUP_FREE_SPACE_RESERVE = 64 * 1024 * 1024


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


def sqlite_sidecar_paths(path: str | Path) -> tuple[Path, ...]:
    database = Path(path)
    return tuple(
        database.with_name(database.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )


def remove_sqlite_copy(path: str | Path) -> None:
    """Remove an owned, closed SQLite copy and all of its private sidecars."""
    database = Path(path)
    database.unlink(missing_ok=True)
    for sidecar in sqlite_sidecar_paths(database):
        sidecar.unlink(missing_ok=True)


def _remove_sqlite_sidecars(path: str | Path) -> None:
    for sidecar in sqlite_sidecar_paths(path):
        sidecar.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    # Windows' CRT rejects fsync on some read-only descriptors.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def ensure_free_space(
    directory: str | Path,
    required_bytes: int,
    *,
    operation: str,
) -> None:
    if required_bytes < 0:
        raise ValueError("required_bytes must not be negative")
    resolved = Path(directory).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(resolved).free
    if free < required_bytes:
        raise CatalogError(
            f"insufficient disk space for {operation}: need {required_bytes} bytes, "
            f"available {free} bytes at {resolved}"
        )


def _configure(connection: sqlite3.Connection, *, query_only: bool = False) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
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
            connection.executescript(CURATION_SCHEMA_SQL)
            connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
            connection.executescript(SEMANTIC_VECTOR_SCHEMA_SQL)
            connection.executescript(TOPIC_SCHEMA_SQL)
            connection.executescript(RELEASE_SCHEMA_SQL)
            connection.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
                (str(CATALOG_SCHEMA_VERSION),),
            )
            connection.execute(f"PRAGMA user_version={CATALOG_SCHEMA_VERSION}")
        elif version in {1, 2, 3, 4, 5} and CATALOG_SCHEMA_VERSION == 6:
            # Public mutating workflows take a verified online backup before
            # reaching this migration. Keep all schema additions and the
            # version bump in one SQLite transaction.
            migration_sql = "BEGIN IMMEDIATE;\n"
            if version == 1:
                migration_sql += CURATION_SCHEMA_SQL + "\n"
            if version in {1, 2}:
                migration_sql += EVIDENCE_GRAPH_SCHEMA_SQL + "\n"
            if version in {1, 2, 3}:
                migration_sql += SEMANTIC_VECTOR_SCHEMA_SQL + "\n"
            if version in {1, 2, 3, 4}:
                migration_sql += TOPIC_SCHEMA_SQL + "\n"
            migration_sql += RELEASE_SCHEMA_SQL
            migration_sql += (
                f"\nUPDATE catalog_meta SET value='{CATALOG_SCHEMA_VERSION}' "
                "WHERE key='schema_version';\n"
                f"PRAGMA user_version={CATALOG_SCHEMA_VERSION};\nCOMMIT;"
            )
            connection.executescript(migration_sql)
        elif version == CATALOG_SCHEMA_VERSION:
            connection.executescript(SCHEMA_SQL)
            connection.executescript(CURATION_SCHEMA_SQL)
            connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
            connection.executescript(SEMANTIC_VECTOR_SCHEMA_SQL)
            connection.executescript(TOPIC_SCHEMA_SQL)
            connection.executescript(RELEASE_SCHEMA_SQL)
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
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise CatalogError(
                "catalog foreign_key_check failed: "
                f"table={foreign_key_error[0]!r}, rowid={foreign_key_error[1]!r}"
            )
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
    if not source.is_file():
        raise CatalogError(f"SQLite backup source does not exist: {source}")
    if source == destination:
        raise CatalogError("SQLite backup source and destination must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    remove_sqlite_copy(destination)
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    completed = False
    try:
        src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(destination)
        src.execute("PRAGMA query_only=ON")
        src.execute("PRAGMA trusted_schema=OFF")
        src.execute("PRAGMA busy_timeout=15000")
        dst.execute("PRAGMA synchronous=FULL")

        def progress(status: int, remaining: int, total: int) -> None:
            if progress_hook is not None:
                progress_hook(status, remaining, total)
            time.sleep(0)

        src.backup(dst, pages=pages, progress=progress, sleep=0.05)
        dst.commit()
        journal_mode = str(dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise CatalogError(
                f"failed to make SQLite backup self-contained: journal_mode={journal_mode}"
            )
        dst.commit()
        completed = True
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        if not completed:
            remove_sqlite_copy(destination)
    try:
        _remove_sqlite_sidecars(destination)
        _fsync_file(destination)
    except BaseException:
        remove_sqlite_copy(destination)
        raise


def verify_sqlite(path: str | Path) -> None:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise CatalogError(f"SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise CatalogError(f"SQLite quick_check failed for {path}: {result}")
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise CatalogError(
                f"SQLite foreign_key_check failed for {path}: "
                f"table={foreign_key_error[0]!r}, rowid={foreign_key_error[1]!r}"
            )
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
    ensure_free_space(
        path.parent,
        path.stat().st_size + _BACKUP_FREE_SPACE_RESERVE,
        operation="catalog backup",
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = path.parent / "backups" / f"catalog-{stamp}.db"
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    try:
        online_backup(path, destination, progress_hook=progress_hook)
        verify_sqlite(destination)
        digest = file_sha256(destination)
        if digest == hashlib.sha256(b"").hexdigest():
            raise CatalogError("catalog backup unexpectedly produced an empty file")
        if file_sha256(destination) != digest:
            raise CatalogError("catalog backup changed during hash readback")
        _atomic_write_text(checksum_path, digest + "\n")
    except BaseException:
        remove_sqlite_copy(destination)
        checksum_path.unlink(missing_ok=True)
        raise
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
        self._failure: BaseException | None = None

    async def __aenter__(self) -> "CatalogWriter":
        if self._task is not None:
            raise RuntimeError("CatalogWriter has already been started")
        self._failure = None
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        try:
            await self.close()
        except BaseException:
            if exc is None:
                raise

    async def _await_while_running(self, awaitable):  # noqa: ANN001, ANN202
        task = self._task
        if task is None:
            raise RuntimeError("CatalogWriter has not been started")
        if isinstance(awaitable, asyncio.Future) and awaitable.done():
            return await awaitable
        if task.done():
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            await task
            raise RuntimeError("CatalogWriter stopped unexpectedly")
        operation = asyncio.ensure_future(awaitable)
        _done, _pending = await asyncio.wait(
            {operation, task}, return_when=asyncio.FIRST_COMPLETED
        )
        if operation.done():
            return await operation
        operation.cancel()
        with suppress(asyncio.CancelledError):
            await operation
        await task
        raise RuntimeError("CatalogWriter stopped unexpectedly")

    async def execute(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        transactional: bool = True,
    ) -> T:
        if self._task is None:
            raise RuntimeError("CatalogWriter has not been started")
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        await self._await_while_running(
            self.queue.put(_WriteCommand(action, future, transactional))
        )
        return await self._await_while_running(future)

    async def close(self) -> None:
        if self._task is None:
            return
        task = self._task
        try:
            if task.done():
                await task
                return
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            await self._await_while_running(
                self.queue.put(_WriteCommand(None, future, False))
            )
            await self._await_while_running(future)
            await task
        finally:
            self._task = None

    @staticmethod
    def _set_exception(future: asyncio.Future[Any], exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                command = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                self._set_exception(command.future, exc)
            finally:
                self.queue.task_done()

    async def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = connect_catalog(self.path)
            while True:
                command = await self.queue.get()
                try:
                    if command.action is None:
                        if not command.future.done():
                            command.future.set_result(None)
                        return
                    if command.transactional:
                        try:
                            connection.execute("BEGIN IMMEDIATE")
                        except BaseException as exc:
                            self._set_exception(command.future, exc)
                            raise
                    try:
                        result = command.action(connection)
                    except BaseException as exc:
                        try:
                            if command.transactional and connection.in_transaction:
                                connection.rollback()
                        except BaseException as rollback_exc:
                            self._set_exception(command.future, rollback_exc)
                            raise
                        self._set_exception(command.future, exc)
                        continue
                    if command.transactional and connection.in_transaction:
                        try:
                            connection.commit()
                        except BaseException as exc:
                            with suppress(BaseException):
                                if connection.in_transaction:
                                    connection.rollback()
                            self._set_exception(command.future, exc)
                            raise
                    if not command.future.done():
                        command.future.set_result(result)
                finally:
                    self.queue.task_done()
        except BaseException as exc:
            self._failure = exc
            self._fail_pending(exc)
            raise
        finally:
            if connection is not None:
                connection.close()


__all__ = [
    "CatalogError",
    "CatalogWriter",
    "backup_existing_catalog",
    "catalog_write_lock",
    "connect_catalog_read_only",
    "ensure_free_space",
    "file_sha256",
    "initialize_catalog",
    "json_dumps",
    "json_loads",
    "online_backup",
    "remove_sqlite_copy",
    "sqlite_sidecar_paths",
    "snapshot_protection_reasons",
    "utcnow_iso",
    "verify_sqlite",
]
