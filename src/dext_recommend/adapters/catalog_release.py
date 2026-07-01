"""CatalogReleaseAdapter: composes reader + mapping + sampling, implements CatalogReleasePort.

The reader returns raw catalog DB columns (id, graph_schema_version,
vector_schema_version, finished_at, ...).  The shared mapping function in
``_mappings.map_catalog_release`` expects domain-shaped keys
(build_id, catalog_schema_version, qdrant_payload_schema_version,
expected_professor_count, created_at).  This adapter performs that
dialect translation before delegating to the pure mapping function, so the
mappings remain reusable by a future CatalogPgReader.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from dext_recommend.adapters._catalog_reader import CatalogReleaseReader
from dext_recommend.adapters._mappings import map_catalog_release, map_professor_sample
from dext_recommend.adapters._sampling import deterministic_sample_ids
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, ProfessorReleaseSample,
)


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO timestamp; fall back to epoch if missing/unparseable."""
    if value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _to_observation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Rename raw catalog columns to the domain keys the mapping expects."""
    active_ids: tuple[str, ...] = row.get("active_entity_ids") or ()
    finished_at = row.get("finished_at")
    started_at = row.get("started_at")
    created_at = finished_at or started_at
    return {
        "build_id": str(row["id"]),
        "catalog_schema_version": row["graph_schema_version"],
        "qdrant_payload_schema_version": row["vector_schema_version"],
        "embedding_provider": row["embedding_provider"],
        "embedding_model": row["embedding_model"],
        "embedding_dimension": row["embedding_dimension"],
        "embedding_fingerprint": row["embedding_fingerprint"],
        "taxonomy_version": row.get("taxonomy_version"),
        "expected_professor_count": len(active_ids),
        "sample_entity_ids": (),  # filled by caller; see read_active
        "created_at": _parse_dt(created_at),
    }


class CatalogReleaseAdapter:
    def __init__(self, reader: CatalogReleaseReader, *, sample_size: int) -> None:
        self._reader = reader
        self._sample_size = sample_size

    async def read_active(self) -> CatalogReleaseObservation | None:
        row = await self._reader.read_active_row()
        if row is None:
            return None
        active_ids: tuple[str, ...] = row.get("active_entity_ids") or ()
        sample_ids = deterministic_sample_ids(active_ids, self._sample_size)
        obs_row = _to_observation_row(row)
        obs_row["sample_entity_ids"] = sample_ids
        return map_catalog_release(obs_row, sample_ids)

    async def read_samples(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[ProfessorReleaseSample, ...]:
        rows = await self._reader.read_sample_rows(build_id, sample_ids)
        return tuple(map_professor_sample(r) for r in rows)


__all__ = ["CatalogReleaseAdapter"]
