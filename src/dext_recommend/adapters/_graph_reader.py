"""Neo4j dialect seam: read active build pointer + sample professor nodes.

Never imports dext_graph's neo4j_sink; only the neo4j async driver API.
"""
from __future__ import annotations

from typing import Any, Protocol

from dext_recommend.ports.release_readback import ReadinessSourceError

_POINTER_QUERY = (
    "MATCH (:GraphState {name:'active'})-[:POINTS_TO]->(build:Build) "
    "RETURN build.id AS build_id ORDER BY build.id"
)
_SAMPLE_QUERY = (
    "MATCH (n:Professor {build_id: $build_id}) "
    "WHERE n.logical_id IN $ids "
    "OPTIONAL MATCH (n)-[:AFFILIATED_WITH {build_id: $build_id}]->"
    "(org:OrgUnit {build_id: $build_id}) "
    "WITH n, org.logical_id AS org_unit_id ORDER BY org_unit_id "
    "RETURN n.logical_id AS entity_id, n.profile_hash AS profile_hash, "
    "n.role_status AS role_status, n.master_eligibility AS master_eligibility, "
    "n.phd_eligibility AS phd_eligibility, collect(DISTINCT org_unit_id) AS org_unit_ids"
)


class GraphReleaseReader(Protocol):
    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None: ...


class Neo4jReader:
    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database or None

    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        try:
            await self._driver.verify_connectivity()
            async with self._driver.session(database=self._database) as session:
                result = await session.run(_POINTER_QUERY)
                values = [str(r["build_id"]) async for r in result]
        except Exception as exc:
            raise ReadinessSourceError("neo4j", "pointer readback failed", retryable=True) from exc
        if not values:
            return None
        if len(values) > 1:
            raise ReadinessSourceError("neo4j", "active pointer targets multiple builds")
        build_id = values[0]
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(_SAMPLE_QUERY, build_id=build_id, ids=list(sample_ids))
                samples = [dict(r) async for r in result]
        except Exception as exc:
            raise ReadinessSourceError("neo4j", "sample readback failed", retryable=True) from exc
        return {"build_id": build_id, "samples": samples}


__all__ = ["GraphReleaseReader", "Neo4jReader"]
