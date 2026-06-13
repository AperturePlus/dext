"""Per-university DB lifecycle: path resolution, fresh (backup + rebuild) and
resume (keep + reconcile). Backup is copy-then-delete (safety first): if the
copy fails, fresh aborts and the original DB is left intact (spec section 4).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from dext.seed import db_filename
from dext.storage.db import (
    StorageError,
    StorageHandle,
    create_all,
    create_engine_for_path,
    ensure_columns,
    make_session_factory,
)
from dext.storage.models import (
    ExtractionAttempt,
    GraphNode,
    NodeStatus,
    OrgUnit,
    SCHEMA_VERSION,
    UniversityMeta,
)
from dext.storage.writer import DBWriter


def resolve_db_path(abbr: str, settings) -> Path:
    """data_dir/<abbr>.db (path assembly; filename from SP1's db_filename)."""
    return Path(settings.data_dir) / db_filename(abbr)


def _backup_existing(db_path: Path, settings) -> Path:
    """Copy db_path (+ -wal/-shm) into backup/<ts>-<abbr>/, then delete originals.
    Raises (leaving the original intact) if the copy fails."""
    abbr = db_path.stem
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(settings.data_dir) / "backup" / f"{ts}-{abbr}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(db_path, backup_dir / db_path.name)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, backup_dir / sidecar.name)
    except OSError as exc:  # copy failed -> abort, keep original
        raise StorageError(f"backup of {db_path} failed, aborting fresh: {exc}") from exc
    # copy succeeded -> remove originals
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix) if suffix else db_path
        target.unlink(missing_ok=True)
    return backup_dir


async def _assemble_handle(engine) -> StorageHandle:
    session_factory = make_session_factory(engine)
    writer = DBWriter(session_factory)
    handle = StorageHandle(engine, writer, session_factory)
    handle.start_writer()
    return handle


async def open_fresh(university, abbr: str, settings) -> StorageHandle:
    db_path = resolve_db_path(abbr, settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        _backup_existing(db_path, settings)  # raises on failure -> original kept
    engine = create_engine_for_path(db_path)
    await create_all(engine)
    handle = await _assemble_handle(engine)
    async with handle.session() as session:
        session.add(UniversityMeta(
            name=university.name,
            start_url=university.url,
            location=getattr(university, "location", None),
            abbr=abbr,
            schema_version=SCHEMA_VERSION,
            crawl_status="pending",
        ))
        await session.commit()
        handle.university_id = (await session.execute(select(UniversityMeta.id))).scalar_one()
    return handle


async def open_resume(university, abbr: str, settings) -> StorageHandle:
    db_path = resolve_db_path(abbr, settings)
    if not db_path.exists():
        raise StorageError(f"no DB at {db_path}; run a fresh crawl first")
    engine = create_engine_for_path(db_path)
    await create_all(engine)      # idempotent (new tables only)
    await ensure_columns(engine)  # heal added columns (spec section 7)
    # Reconcile crash residue BEFORE starting the writer (startup is single-threaded).
    async with engine.begin() as conn:
        await conn.execute(
            update(GraphNode).where(GraphNode.status == NodeStatus.in_progress).values(status=NodeStatus.retry)
        )
        await conn.execute(
            update(OrgUnit).where(OrgUnit.status == "in_progress").values(status="pending")
        )
        await conn.execute(
            update(ExtractionAttempt).where(ExtractionAttempt.status == "running").values(status="retry")
        )
    return await _assemble_handle(engine)
