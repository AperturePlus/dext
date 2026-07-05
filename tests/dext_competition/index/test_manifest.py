"""C1 manifest tests — content_hash aggregation + deterministic serialization.

``content_hash`` aggregates all chunk hashes deterministically: sort the
chunk_hash strings ascending, join with newline, SHA-256. ``version_id`` is
derived from content_hash (``kb-v1-<content_hash[:12]>``) so it is a stable
external ``knowledge_base_version``. Serialization to/from JSON is
deterministic and round-trips.
"""
from __future__ import annotations

import datetime
import hashlib
import json

from dext_competition import Chunk, KnowledgeBaseManifest
from dext_competition.index.manifest import (
    content_hash,
    manifest_to_json,
    manifest_from_json,
    version_id,
)


def _chunk(doc: str, hpath: str, text: str) -> Chunk:
    return Chunk(
        doc_path=doc,
        heading_path=hpath,
        chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        source_links=(),
        last_verified=None,
    )


def test_content_hash_is_sha256_of_sorted_joined_chunk_hashes():
    chunks = (
        _chunk("a.md", "a.md > A", "aaa"),
        _chunk("b.md", "b.md > B", "bbb"),
    )
    hashes = sorted([c.chunk_hash for c in chunks])
    expected = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
    assert content_hash(chunks) == expected


def test_content_hash_independent_of_chunk_order():
    a = _chunk("a.md", "a.md > A", "aaa")
    b = _chunk("b.md", "b.md > B", "bbb")
    assert content_hash((a, b)) == content_hash((b, a))


def test_content_hash_changes_when_content_changes():
    a = (_chunk("a.md", "a.md > A", "aaa"),)
    b = (_chunk("a.md", "a.md > A", "different"),)
    assert content_hash(a) != content_hash(b)


def test_version_id_is_prefixed_content_hash_prefix():
    chunks = (_chunk("a.md", "a.md > A", "aaa"),)
    ch = content_hash(chunks)
    assert version_id(ch) == f"kb-v1-{ch[:12]}"


def test_manifest_to_json_is_deterministic_round_trip():
    chunks = (
        _chunk("a.md", "a.md > A", "aaa"),
        _chunk("b.md", "b.md > B", "bbb"),
    )
    ch = content_hash(chunks)
    m = KnowledgeBaseManifest(
        version_id=version_id(ch),
        source_root="data/竞赛助手/",
        file_count=2,
        markdown_file_count=2,
        content_hash=ch,
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )
    j1 = manifest_to_json(m)
    j2 = manifest_to_json(m)
    assert j1 == j2  # byte-identical
    restored = manifest_from_json(j1)
    assert restored == m


def test_manifest_to_json_ensure_ascii_false_preserves_chinese():
    m = KnowledgeBaseManifest(
        version_id="kb-v1-x",
        source_root="data/竞赛助手/",
        file_count=1,
        markdown_file_count=1,
        content_hash="abc",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )
    j = manifest_to_json(m)
    assert "竞赛助手" in j  # not escaped to \uXXXX


def test_manifest_to_json_sorted_keys_for_stable_output():
    m = KnowledgeBaseManifest(
        version_id="kb-v1-x",
        source_root="r",
        file_count=1,
        markdown_file_count=1,
        content_hash="abc",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )
    obj = json.loads(manifest_to_json(m))
    # All keys present.
    assert set(obj) == {
        "version_id", "source_root", "file_count", "markdown_file_count",
        "content_hash", "generated_at",
    }


def test_manifest_from_json_round_trip_preserves_generated_at():
    m = KnowledgeBaseManifest(
        version_id="kb-v1-x",
        source_root="r",
        file_count=1,
        markdown_file_count=1,
        content_hash="abc",
        generated_at=datetime.datetime(2026, 7, 2, 12, 30, 5),
    )
    assert manifest_from_json(manifest_to_json(m)) == m
