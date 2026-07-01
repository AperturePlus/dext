"""GraphReleaseAdapter: composes Neo4jReader + mapping, implements GraphReleasePort."""
from __future__ import annotations

from dext_recommend.adapters._graph_reader import GraphReleaseReader
from dext_recommend.adapters._mappings import map_graph_release
from dext_recommend.ports.release_readback import GraphReleaseObservation


class GraphReleaseAdapter:
    def __init__(self, reader: GraphReleaseReader) -> None:
        self._reader = reader

    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> GraphReleaseObservation | None:
        raw = await self._reader.read_active(sample_ids)
        if raw is None:
            return None
        return map_graph_release(raw)


__all__ = ["GraphReleaseAdapter"]
