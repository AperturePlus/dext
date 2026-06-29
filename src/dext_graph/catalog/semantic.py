"""Stage-4 semantic profile, vector cache, and sparse-vector primitives."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from dataclasses import asdict, dataclass
from typing import Any

from dext_graph.profiles import TEMPLATES, TextTokenizer, normalize_text
from dext_graph.catalog.db import utcnow_iso

_TOKEN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_]+")


class CacheVectorCorruption(RuntimeError):
    """A cached vector BLOB failed checksum, size, or finite-value validation."""


@dataclass(frozen=True)
class SemanticProfile:
    entity_id: str
    template_version: str
    tokenizer_identity: str
    normalized_profile: str
    profile_hash: str
    token_count: int
    payload: dict[str, Any]

    def asdict(self) -> dict[str, Any]:
        value = asdict(self)
        value["payload"] = dict(self.payload)
        return value


@dataclass(frozen=True)
class CachedVector:
    dense: list[float]
    sparse: dict[str, Any]
    vector_checksum: str


def _bounded(value: str | None, budget: int, tokenizer: TextTokenizer) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    token_ids = tokenizer.encode(normalized, add_special_tokens=False)
    if len(token_ids) <= budget:
        return normalized
    return normalize_text(tokenizer.decode(token_ids[:budget]))


def _line(label: str, content: str) -> str | None:
    return f"{label}：{content}" if content else None


def build_semantic_profile(
    professor: dict[str, Any],
    *,
    research_statements: list[str] | tuple[str, ...],
    publication_mentions: list[str] | tuple[str, ...],
    approved_topics: list[str] | tuple[str, ...],
    bio: str | None,
    template_name: str,
    tokenizer: TextTokenizer,
    max_tokens: int,
) -> SemanticProfile:
    if template_name not in TEMPLATES:
        raise ValueError(f"unknown profile template: {template_name}")
    budget = TEMPLATES[template_name]
    entity_id = str(professor["entity_id"])
    org_units = professor.get("org_units") or []
    if isinstance(org_units, str):
        org_units = [org_units]
    research_text = "\n".join(normalize_text(value) for value in research_statements if normalize_text(value))
    topic_text = "\n".join(normalize_text(value) for value in approved_topics if normalize_text(value))
    publication_text = "\n".join(
        normalize_text(value) for value in publication_mentions if normalize_text(value)
    )

    school = _bounded(str(professor.get("university") or ""), 64, tokenizer)
    org = _bounded("、".join(str(value) for value in org_units if str(value).strip()), 128, tokenizer)
    title = _bounded(str(professor.get("title") or ""), 64, tokenizer)
    research = _bounded(research_text, budget.research_areas, tokenizer)
    topics = _bounded(topic_text, 768, tokenizer)
    publications = _bounded(publication_text, budget.publications, tokenizer)
    bounded_bio = _bounded(bio, budget.bio, tokenizer)
    lines = [
        _line("学校", school),
        _line("学院", org),
        _line("职称", title),
        _line("研究方向原文", research),
        _line("规范主题", topics),
        _line("代表成果", publications),
        _line("简介", bounded_bio),
    ]
    normalized_profile = "\n".join(line for line in lines if line)
    token_ids = tokenizer.encode(normalized_profile, add_special_tokens=False)
    if len(token_ids) > max_tokens:
        normalized_profile = "\n".join(
            line.strip()
            for line in tokenizer.decode(token_ids[:max_tokens]).splitlines()
            if line.strip()
        )
        token_ids = tokenizer.encode(normalized_profile, add_special_tokens=False)
    profile_hash = hashlib.sha256(
        f"{template_name}\n{normalized_profile}".encode("utf-8")
    ).hexdigest()
    return SemanticProfile(
        entity_id=entity_id,
        template_version=template_name,
        tokenizer_identity=tokenizer.identity,
        normalized_profile=normalized_profile,
        profile_hash=profile_hash,
        token_count=len(token_ids),
        payload=dict(professor.get("payload") or {}),
    )


def dense_vector_to_blob(vector: list[float] | tuple[float, ...]) -> bytes:
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("dense vector contains a non-finite value")
    return struct.pack("<" + "f" * len(values), *values)


def dense_vector_from_blob(blob: bytes, *, dimension: int, checksum: str) -> list[float]:
    if hashlib.sha256(blob).hexdigest() != checksum:
        raise CacheVectorCorruption("dense vector checksum mismatch")
    expected = dimension * 4
    if len(blob) != expected:
        raise CacheVectorCorruption(
            f"dense vector BLOB has {len(blob)} bytes; expected {expected}"
        )
    values = list(struct.unpack("<" + "f" * dimension, blob))
    if not all(math.isfinite(value) for value in values):
        raise CacheVectorCorruption("dense vector contains a non-finite value")
    return values


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN.finditer(normalize_text(text))]


def sparse_bm25_vector(text: str, *, tokenizer_version: str) -> dict[str, list[float] | list[int]]:
    counts: dict[str, int] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0) + 1
    rows: list[tuple[int, float]] = []
    for token, count in counts.items():
        digest = hashlib.sha256(f"{tokenizer_version}\0{token}".encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
        # Single-document BM25-style saturation. The stable tokenizer/versioned
        # index is what matters for cache compatibility at this stage.
        value = float((count * 2.2) / (count + 1.2))
        rows.append((index, value))
    rows.sort(key=lambda item: item[0])
    return {
        "indices": [index for index, _ in rows],
        "values": [value for _, value in rows],
    }


def sparse_vector_to_blob(vector: dict[str, Any]) -> bytes:
    return json.dumps(vector, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sparse_vector_from_blob(blob: bytes) -> dict[str, Any]:
    value = json.loads(blob.decode("utf-8"))
    if not isinstance(value, dict):
        raise CacheVectorCorruption("sparse vector BLOB is not an object")
    indices = value.get("indices")
    values = value.get("values")
    if not isinstance(indices, list) or not isinstance(values, list) or len(indices) != len(values):
        raise CacheVectorCorruption("sparse vector has invalid indices/values")
    converted_indices = [int(item) for item in indices]
    converted_values = [float(item) for item in values]
    if converted_indices != sorted(converted_indices):
        raise CacheVectorCorruption("sparse vector indices are not sorted")
    if not all(math.isfinite(item) for item in converted_values):
        raise CacheVectorCorruption("sparse vector contains a non-finite value")
    return {"indices": converted_indices, "values": converted_values}


def vector_checksum(
    dense_vector: list[float] | tuple[float, ...],
    sparse_vector: dict[str, Any],
) -> str:
    dense_blob = dense_vector_to_blob(dense_vector)
    sparse_blob = sparse_vector_to_blob(sparse_vector)
    digest = hashlib.sha256()
    digest.update(dense_blob)
    digest.update(b"\0")
    digest.update(sparse_blob)
    return digest.hexdigest()


def cache_blob_checksum(dense_blob: bytes, sparse_blob: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(dense_blob)
    digest.update(b"\0")
    digest.update(sparse_blob)
    return digest.hexdigest()


def store_embedding_cache(
    connection: sqlite3.Connection,
    *,
    profile_hash: str,
    embedding_fingerprint: str,
    dense_vector: list[float] | tuple[float, ...],
    sparse_vector: dict[str, Any],
) -> str:
    dense_blob = dense_vector_to_blob(dense_vector)
    sparse_blob = sparse_vector_to_blob(sparse_vector)
    checksum = cache_blob_checksum(dense_blob, sparse_blob)
    connection.execute(
        """
        INSERT INTO embedding_cache(
          profile_hash, embedding_fingerprint, dense_blob, sparse_blob,
          vector_checksum, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_hash, embedding_fingerprint) DO UPDATE SET
          dense_blob=excluded.dense_blob,
          sparse_blob=excluded.sparse_blob,
          vector_checksum=excluded.vector_checksum
        """,
        (
            profile_hash,
            embedding_fingerprint,
            dense_blob,
            sparse_blob,
            checksum,
            utcnow_iso(),
        ),
    )
    return checksum


def load_embedding_cache(
    connection: sqlite3.Connection,
    *,
    profile_hash: str,
    embedding_fingerprint: str,
    dimension: int,
) -> CachedVector | None:
    row = connection.execute(
        """
        SELECT dense_blob, sparse_blob, vector_checksum FROM embedding_cache
        WHERE profile_hash=? AND embedding_fingerprint=?
        """,
        (profile_hash, embedding_fingerprint),
    ).fetchone()
    if row is None:
        return None
    dense_blob = bytes(row[0])
    sparse_blob = bytes(row[1])
    checksum = str(row[2])
    if cache_blob_checksum(dense_blob, sparse_blob) != checksum:
        raise CacheVectorCorruption("cached vector checksum mismatch")
    dense = dense_vector_from_blob(
        dense_blob, dimension=dimension, checksum=hashlib.sha256(dense_blob).hexdigest()
    )
    sparse = sparse_vector_from_blob(sparse_blob)
    return CachedVector(dense=dense, sparse=sparse, vector_checksum=checksum)


def mark_embedding_job(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    entity_id: str,
    profile_hash: str,
    status: str,
    increment_attempt: bool = False,
    vector_checksum: str | None = None,
    last_error: str | None = None,
) -> None:
    if status not in {"pending", "running", "retry", "succeeded", "terminal-invalid-input"}:
        raise ValueError(f"invalid embedding job status: {status}")
    if status == "succeeded":
        last_error = None
    connection.execute(
        """
        INSERT INTO embedding_jobs(
          build_id, entity_id, profile_hash, status, attempt_count,
          vector_checksum, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(build_id, entity_id) DO UPDATE SET
          profile_hash=excluded.profile_hash,
          status=excluded.status,
          attempt_count=embedding_jobs.attempt_count + ?,
          vector_checksum=excluded.vector_checksum,
          last_error=excluded.last_error,
          updated_at=excluded.updated_at
        """,
        (
            build_id,
            entity_id,
            profile_hash,
            status,
            1 if increment_attempt else 0,
            vector_checksum,
            last_error,
            utcnow_iso(),
            1 if increment_attempt else 0,
        ),
    )


__all__ = [
    "CacheVectorCorruption",
    "CachedVector",
    "SemanticProfile",
    "build_semantic_profile",
    "cache_blob_checksum",
    "dense_vector_from_blob",
    "dense_vector_to_blob",
    "load_embedding_cache",
    "mark_embedding_job",
    "sparse_bm25_vector",
    "sparse_vector_from_blob",
    "sparse_vector_to_blob",
    "store_embedding_cache",
    "vector_checksum",
]
