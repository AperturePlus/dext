"""Reproducible C1 index build and staged artifact publication."""
from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
from pathlib import Path
import shutil
import tempfile

from dext_competition.contracts.knowledge import KnowledgeBaseManifest
from dext_competition.index.artifacts import (
    BM25_FILE,
    CHUNKS_FILE,
    MANIFEST_FILE,
    IndexArtifactError,
    chunks_to_jsonl,
)
from dext_competition.index.bm25 import BM25Index
from dext_competition.index.chunker import chunk_document
from dext_competition.index.manifest import (
    content_hash,
    manifest_to_json,
    version_id,
)
from dext_competition.index.markdown import parse_markdown
from dext_competition.index.scanner import scan_markdown_root


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    manifest: KnowledgeBaseManifest
    chunk_count: int
    artifact_dir: Path


def _stable_generated_at(last_verified_values: tuple[str, ...]) -> datetime.datetime:
    if not last_verified_values:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    try:
        latest = max(datetime.date.fromisoformat(value) for value in last_verified_values)
    except ValueError as exc:
        raise IndexArtifactError(f"invalid last_verified date: {exc}") from exc
    return datetime.datetime.combine(
        latest, datetime.time.min, tzinfo=datetime.timezone.utc
    )


def _source_root_label(source_root: str | Path) -> str:
    value = Path(source_root).as_posix().rstrip("/")
    return value + "/"


def _write_artifacts(stage: Path, manifest: KnowledgeBaseManifest, chunks, bm25) -> None:
    stage.mkdir(parents=True, exist_ok=False)
    (stage / MANIFEST_FILE).write_text(manifest_to_json(manifest), encoding="utf-8")
    (stage / CHUNKS_FILE).write_text(chunks_to_jsonl(chunks), encoding="utf-8")
    (stage / BM25_FILE).write_text(
        json.dumps(
            bm25.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _promote(stage: Path, output: Path) -> None:
    backup: Path | None = None
    try:
        if output.exists():
            backup = Path(
                tempfile.mkdtemp(prefix=f".{output.name}.backup-", dir=output.parent)
            )
            backup.rmdir()
            output.replace(backup)
        stage.replace(output)
    except Exception:
        if backup is not None and backup.exists() and not output.exists():
            backup.replace(output)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)


def build_index(
    source_root: str | Path = "data/竞赛助手/",
    output_dir: str | Path = "data/competition/index/",
) -> IndexBuildResult:
    """Build all three canonical artifacts and publish them as one directory."""

    source = Path(source_root)
    output = Path(output_dir)
    if output.exists() and not output.is_dir():
        raise IndexArtifactError(f"index output is not a directory: {output}")
    files = scan_markdown_root(str(source))
    chunks = tuple(
        chunk
        for markdown_file in files
        for chunk in chunk_document(
            parse_markdown(markdown_file.path, markdown_file.content)
        )
    )
    aggregate_hash = content_hash(chunks)
    verified = tuple(
        sorted({chunk.last_verified for chunk in chunks if chunk.last_verified})
    )
    manifest = KnowledgeBaseManifest(
        version_id=version_id(aggregate_hash),
        source_root=_source_root_label(source_root),
        file_count=len(files),
        markdown_file_count=len(files),
        content_hash=aggregate_hash,
        generated_at=_stable_generated_at(verified),
    )
    bm25 = BM25Index.from_chunks(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    stage.rmdir()
    try:
        _write_artifacts(stage, manifest, chunks, bm25)
        _promote(stage, output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return IndexBuildResult(manifest, len(chunks), output)


__all__ = ["IndexBuildResult", "build_index"]
