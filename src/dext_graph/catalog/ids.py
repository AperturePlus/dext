"""Deterministic catalog hashes plus UUIDv7 allocation."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import unicodedata
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DECORATIVE_DOTS = dict.fromkeys(map(ord, "·•∙・･‧"), None)


def uuid7() -> str:
    """Return an RFC 9562 UUIDv7 without requiring a third-party package."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return str(uuid.UUID(int=value))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_parts(*parts: object) -> str:
    encoded = "\0".join(str(part) for part in parts).encode("utf-8")
    return sha256_bytes(encoded)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.strip())
    return normalized.translate(_DECORATIVE_DOTS).casefold()


def canonical_source_url(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        host = (parts.hostname or "").lower()
        if not host:
            return None
        port = parts.port
    except ValueError:
        return None
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def snapshot_id(university_id: str, file_hash: str) -> str:
    return hash_parts(university_id, file_hash)


def source_document_id(url: str, content_hash: str) -> str:
    return hash_parts(url, content_hash)


def observation_id(
    university_id: str,
    source_identity: str,
    normalized_name: str,
    row_hash: str,
) -> str:
    return hash_parts(university_id, source_identity, normalized_name, row_hash)


def finding_id(build_id: str, code: str, reference: str) -> str:
    return hash_parts(build_id, code, reference)


__all__ = [
    "canonical_json",
    "canonical_json_hash",
    "canonical_source_url",
    "finding_id",
    "hash_parts",
    "name_key",
    "observation_id",
    "sha256_bytes",
    "snapshot_id",
    "source_document_id",
    "uuid7",
]
