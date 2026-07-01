import pytest
from sqlalchemy import select

from dext.storage.db import (
    StorageError,
    create_engine_for_path,
    create_all,
    ensure_columns,
    make_session_factory,
)
from dext.storage.models import GraphNode, NodeStatus, NodeType


async def _engine(tmp_path):
    eng = create_engine_for_path(tmp_path / "t.db")
    await create_all(eng)
    return eng


async def test_pragmas_applied(tmp_path):
    eng = await _engine(tmp_path)
    async with eng.connect() as conn:
        jm = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    assert jm.lower() == "wal"
    assert fk == 1
    await eng.dispose()


async def test_round_trip_insert_and_read(tmp_path):
    eng = await _engine(tmp_path)
    sf = make_session_factory(eng)
    async with sf() as s:
        s.add(GraphNode(node_key="k1", type=NodeType.detail_url, url="https://x", status=NodeStatus.pending))
        await s.commit()
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.node_key == "k1"))).scalar_one()
        assert node.type == "detail_url"
        assert node.status == "pending"
        assert node.attempt_count == 0
        assert node.max_attempts == 3
        assert node.created_at  # timestamp populated
    await eng.dispose()


async def test_json_column_preserves_unicode(tmp_path):
    # ensure_ascii=False must keep Chinese readable in the stored JSON text.
    eng = await _engine(tmp_path)
    sf = make_session_factory(eng)
    async with sf() as s:
        s.add(GraphNode(node_key="k2", type=NodeType.org_unit, url="about:org_unit:x",
                        metadata_json={"discovery_source": "数学学院"}))
        await s.commit()
    async with eng.connect() as conn:
        raw = (await conn.exec_driver_sql(
            "SELECT metadata_json FROM crawl_graph_nodes WHERE node_key='k2'"
        )).scalar()
    assert "数学学院" in raw  # not \uXXXX-escaped
    await eng.dispose()


async def test_ensure_columns_adds_missing_column(tmp_path):
    # Simulate an older DB missing a column, then heal it.
    eng = create_engine_for_path(tmp_path / "old.db")
    async with eng.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE org_units (id INTEGER PRIMARY KEY, name TEXT, url TEXT)")
    await ensure_columns(eng)  # should add kind/status/discovered_from_url/created_at/updated_at
    async with eng.connect() as conn:
        cols = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info('org_units')")).all()}
    assert {"kind", "status", "discovered_from_url", "created_at", "updated_at"} <= cols
    await eng.dispose()


async def test_create_engine_rejects_empty_path():
    with pytest.raises(StorageError):
        create_engine_for_path("")
