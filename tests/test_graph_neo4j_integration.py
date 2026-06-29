import os
import sqlite3

import pytest

from dext_graph.catalog.workflow import create_build
from dext_graph.catalog.db import CatalogWriter
from dext_graph.catalog.neo4j_sink import write_neo4j_exports
from test_catalog_workflow import _patch_runtime, _settings, _source_db


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_neo4j_constraints_merge_and_manifest_readback(tmp_path, monkeypatch):
    uri = os.getenv("DEXT_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("DEXT_TEST_NEO4J_URI is required for real Neo4j integration")
    _patch_runtime(monkeypatch)
    monkeypatch.delenv("DEXT_TEST_SKIP_NEO4J", raising=False)
    settings = _settings(tmp_path, batch=1).model_copy(
        update={"neo4j_uri": uri, "neo4j_database": "neo4j", "build_neo4j_batch": 1}
    )
    _source_db(settings.source_data_dir / "test.db", count=3)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    try:
        assert result["build"]["status"] == "WRITING_VECTOR"
        assert result["graph"]["status"] == "COMPLETED"
        assert any(item["sink"] == "neo4j" for item in result["checkpoints"])
        with sqlite3.connect(settings.catalog_path) as connection:
            connection.execute(
                "DELETE FROM sink_checkpoints WHERE build_id=? AND sink='neo4j'",
                (build_id,),
            )
        async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
            await write_neo4j_exports(writer, build_id, settings)
    finally:
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(uri, auth=None)
        try:
            await driver.execute_query(
                "MATCH (n {build_id: $build_id}) DETACH DELETE n",
                build_id=build_id,
                database_="neo4j",
            )
        finally:
            await driver.close()
