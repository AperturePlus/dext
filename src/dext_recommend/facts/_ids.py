"""Pure entity-id helpers for R4 fact reads: stable dedup + SQLite-safe chunking."""
from __future__ import annotations

from collections.abc import Iterable


def dedupe_entity_ids(ids: Iterable[str] | None) -> tuple[str, ...]:
    """Order-preserving dedup; None -> ()."""
    if ids is None:
        return ()
    return tuple(dict.fromkeys(str(i) for i in ids))


def chunk_entity_ids(
    ids: Iterable[str], limit: int,
) -> tuple[tuple[str, ...], ...]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    ordered = dedupe_entity_ids(ids)
    return tuple(ordered[i:i + limit] for i in range(0, len(ordered), limit))


__all__ = ["chunk_entity_ids", "dedupe_entity_ids"]
