"""VectorReleaseAdapter: composes QdrantReader + mapping, implements VectorReleasePort."""
from __future__ import annotations

from dext_recommend.adapters._mappings import map_vector_release
from dext_recommend.adapters._vector_reader import VectorReleaseReader
from dext_recommend.ports.release_readback import VectorReleaseObservation


class VectorReleaseAdapter:
    def __init__(self, reader: VectorReleaseReader) -> None:
        self._reader = reader

    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> VectorReleaseObservation | None:
        raw = await self._reader.read_current(alias, sample_ids)
        if raw is None:
            return None
        return map_vector_release(raw)


__all__ = ["VectorReleaseAdapter"]
