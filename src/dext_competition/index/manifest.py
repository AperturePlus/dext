"""Knowledge-base manifest: content_hash aggregation + deterministic JSON
serialization (C1 spec §5).

Aggregation order (documented): sort all ``chunk_hash`` strings ascending
(lexicographic on the hex digest), join with ``\\n``, SHA-256 the UTF-8 bytes.
``version_id`` is ``kb-v1-<content_hash[:12]>`` — a stable external
``knowledge_base_version`` derived purely from content. JSON is serialized
with ``ensure_ascii=False`` (UTF-8 end to end), ``sort_keys=True``, fixed
separators and a trailing newline so two clean rebuilds produce byte-identical
output.

``generated_at`` is part of the manifest but is supplied by the builder (a
stable, content-derived value by default so rebuilds are reproducible); it is
NOT part of ``content_hash`` or ``version_id``.
"""
from __future__ import annotations

import datetime
import hashlib
import json

from dext_competition.contracts.knowledge import Chunk, KnowledgeBaseManifest

__all__ = [
    "content_hash",
    "version_id",
    "manifest_to_json",
    "manifest_from_json",
]


def content_hash(chunks: tuple[Chunk, ...] | list[Chunk]) -> str:
    """Aggregate all chunk hashes deterministically.

    Order: sort chunk_hash strings ascending; join with newline; SHA-256.
    """
    hashes = sorted(c.chunk_hash for c in chunks)
    joined = "\n".join(hashes)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def version_id(content_hash_value: str) -> str:
    """Stable external ``knowledge_base_version`` derived from content_hash."""
    return f"kb-v1-{content_hash_value[:12]}"


def manifest_to_json(manifest: KnowledgeBaseManifest) -> str:
    """Deterministic JSON serialization (UTF-8, sorted keys, trailing newline)."""
    obj = {
        "version_id": manifest.version_id,
        "source_root": manifest.source_root,
        "file_count": manifest.file_count,
        "markdown_file_count": manifest.markdown_file_count,
        "content_hash": manifest.content_hash,
        "generated_at": manifest.generated_at.isoformat(),
    }
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"


def manifest_from_json(data: str) -> KnowledgeBaseManifest:
    """Inverse of :func:`manifest_to_json`."""
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ValueError("manifest must be a JSON object")
    expected = {
        "version_id",
        "source_root",
        "file_count",
        "markdown_file_count",
        "content_hash",
        "generated_at",
    }
    if set(obj) != expected:
        raise ValueError("manifest fields do not match artifact schema")
    for field in ("version_id", "source_root", "content_hash", "generated_at"):
        if not isinstance(obj[field], str):
            raise ValueError(f"manifest {field} must be a string")
    for field in ("file_count", "markdown_file_count"):
        if isinstance(obj[field], bool) or not isinstance(obj[field], int) or obj[field] < 0:
            raise ValueError(f"manifest {field} must be a non-negative integer")
    return KnowledgeBaseManifest(
        version_id=obj["version_id"],
        source_root=obj["source_root"],
        file_count=obj["file_count"],
        markdown_file_count=obj["markdown_file_count"],
        content_hash=obj["content_hash"],
        generated_at=datetime.datetime.fromisoformat(obj["generated_at"]),
    )
