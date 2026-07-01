import os
import uuid

import pytest

from dext_graph.catalog.vector_sink import (
    CURRENT_PROFESSOR_ALIAS,
    ProfessorQdrant,
    professor_collection_name,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_qdrant_collection_alias_and_readback():
    url = os.getenv("DEXT_TEST_QDRANT_URL")
    if not url:
        pytest.skip("DEXT_TEST_QDRANT_URL is required for real Qdrant integration")
    from qdrant_client import AsyncQdrantClient, models

    build_id = f"integration-{uuid.uuid4()}"
    collection = professor_collection_name(build_id)
    client = AsyncQdrantClient(url=url)
    sink = ProfessorQdrant(url, client=client)
    previous = await sink.resolve_current_alias()
    try:
        await sink.create_collection(collection, dimension=3)
        await sink.switch_current_alias(collection)
        assert await sink.resolve_current_alias() == collection
        assert await sink.count(collection) == 0
        assert [item async for item in sink.iter_payloads(collection)] == []
    finally:
        current = await sink.resolve_current_alias()
        if previous is not None:
            await sink.switch_current_alias(previous)
        elif current == collection:
            await client.update_collection_aliases(
                change_aliases_operations=[
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(
                            alias_name=CURRENT_PROFESSOR_ALIAS
                        )
                    )
                ]
            )
        if await client.collection_exists(collection):
            await client.delete_collection(collection_name=collection)
        await client.close()
