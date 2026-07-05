"""Stable C1 artifact names and canonical chunk serialization."""
from __future__ import annotations

import json
from typing import Any

from dext_competition.contracts.knowledge import Chunk

MANIFEST_FILE = "manifest.json"
CHUNKS_FILE = "chunks.jsonl"
BM25_FILE = "bm25.json"
ARTIFACT_FILES = (MANIFEST_FILE, CHUNKS_FILE, BM25_FILE)


class IndexArtifactError(RuntimeError):
    """A persisted C1 artifact is missing, malformed, or inconsistent."""


def chunk_to_dict(chunk: Chunk) -> dict[str, Any]:
    return {
        "chunk_hash": chunk.chunk_hash,
        "doc_path": chunk.doc_path,
        "heading_path": chunk.heading_path,
        "last_verified": chunk.last_verified,
        "source_links": list(chunk.source_links),
        "text": chunk.text,
    }


def chunk_from_dict(value: Any) -> Chunk:
    try:
        if not isinstance(value, dict):
            raise TypeError("chunk must be an object")
        expected = {
            "chunk_hash",
            "doc_path",
            "heading_path",
            "last_verified",
            "source_links",
            "text",
        }
        if set(value) != expected:
            raise ValueError("chunk fields do not match artifact schema")
        links = value["source_links"]
        if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
            raise TypeError("source_links must be a string array")
        last_verified = value["last_verified"]
        if last_verified is not None and not isinstance(last_verified, str):
            raise TypeError("last_verified must be a string or null")
        for field in ("chunk_hash", "doc_path", "heading_path", "text"):
            if not isinstance(value[field], str):
                raise TypeError(f"{field} must be a string")
        return Chunk(
            doc_path=value["doc_path"],
            heading_path=value["heading_path"],
            chunk_hash=value["chunk_hash"],
            text=value["text"],
            source_links=tuple(links),
            last_verified=last_verified,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexArtifactError(f"invalid chunk artifact: {exc}") from exc


def chunks_to_jsonl(chunks: tuple[Chunk, ...]) -> str:
    return "".join(
        json.dumps(
            chunk_to_dict(chunk),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for chunk in chunks
    )


def chunks_from_jsonl(data: str) -> tuple[Chunk, ...]:
    chunks: list[Chunk] = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            raise IndexArtifactError(f"blank chunk record at line {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IndexArtifactError(
                f"invalid chunks JSON at line {line_number}: {exc.msg}"
            ) from exc
        chunks.append(chunk_from_dict(value))
    return tuple(chunks)


__all__ = [
    "ARTIFACT_FILES",
    "BM25_FILE",
    "CHUNKS_FILE",
    "IndexArtifactError",
    "MANIFEST_FILE",
    "chunk_from_dict",
    "chunk_to_dict",
    "chunks_from_jsonl",
    "chunks_to_jsonl",
]
