"""Deterministic sample-ID derivation shared across catalog/vector/graph readback.

Stable across calls and across SQLite/PG readers; does not depend on DB sort
order. Used so catalog, Qdrant and Neo4j reconcile the same sample set.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence


def deterministic_sample_ids(
    entity_ids: Sequence[str], k: int,
) -> tuple[str, ...]:
    if k <= 0 or not entity_ids:
        return ()
    keyed = sorted(
        entity_ids,
        key=lambda eid: (hashlib.sha256(eid.encode("utf-8")).hexdigest(), eid),
    )
    return tuple(keyed[:k])


__all__ = ["deterministic_sample_ids"]
