import asyncio

import pytest
from sqlalchemy import select

from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import EdgeType, GraphEdge, GraphNode, NodeStatus, NodeType
from dext.storage.writer import DBWriter, NodeSpec, OrgUnitSpec


async def _writer(tmp_path):
    eng = create_engine_for_path(tmp_path / "w.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    return eng, sf, w, task


async def _close(eng, w, task):
    await w.stop()
    await task
    await eng.dispose()


async def test_upsert_node_returns_id_and_is_idempotent(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    spec = NodeSpec(node_key="n1", type=NodeType.detail_url, url="https://x/1", priority_score=1.0)
    id1 = await w.upsert_node(spec)
    id2 = await w.upsert_node(NodeSpec(node_key="n1", type=NodeType.detail_url, url="https://x/1", priority_score=5.0))
    assert id1 == id2
    async with sf() as s:
        rows = (await s.execute(select(GraphNode).where(GraphNode.node_key == "n1"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].priority_score == 5.0  # re-discovery bumps priority to the max
    await _close(eng, w, task)


async def test_concurrent_submits_are_serialized_and_each_future_resolves(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    specs = [NodeSpec(node_key=f"k{i}", type=NodeType.org_listing_url, url=f"https://x/{i}") for i in range(25)]
    ids = await asyncio.gather(*(w.upsert_node(s) for s in specs))
    assert len(set(ids)) == 25  # all distinct, none lost
    async with sf() as s:
        assert (await s.execute(select(GraphNode))).scalars().all().__len__() == 25
    await _close(eng, w, task)


async def test_add_edge_is_idempotent(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    a = await w.upsert_node(NodeSpec(node_key="a", type=NodeType.org_unit, url="about:org_unit:a"))
    b = await w.upsert_node(NodeSpec(node_key="b", type=NodeType.faculty_list_url, url="https://x/b"))
    e1 = await w.add_edge(a, b, EdgeType.discovered_on_page)
    e2 = await w.add_edge(a, b, EdgeType.discovered_on_page)
    assert e1 == e2
    async with sf() as s:
        assert len((await s.execute(select(GraphEdge))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_mark_node_terminal_sets_completed_at(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="m", type=NodeType.detail_url, url="https://x/m"))
    await w.mark_node(nid, NodeStatus.done, content_hash="abc123")
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == nid))).scalar_one()
        assert node.status == "done"
        assert node.content_hash == "abc123"
        assert node.completed_at is not None
    await _close(eng, w, task)


async def test_claim_next_atomic_and_priority_order(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="lo", type=NodeType.detail_url, url="https://x/lo", priority_score=1.0))
    await w.upsert_node(NodeSpec(node_key="hi", type=NodeType.detail_url, url="https://x/hi", priority_score=9.0))
    claimed = await w.claim_next(run_id=1)
    assert claimed is not None and claimed.node_key == "hi"  # highest priority first
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.node_key == "hi"))).scalar_one()
        assert node.status == "in_progress"
        assert node.attempt_count == 1
        assert node.claimed_at is not None
        assert node.run_id == 1
    await _close(eng, w, task)


async def test_claim_next_honors_exclude_set(tmp_path):
    # SP6 single-run no-reclaim: excluded node_keys are skipped.
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="hi", type=NodeType.detail_url, url="https://x/hi", priority_score=9.0))
    await w.upsert_node(NodeSpec(node_key="lo", type=NodeType.detail_url, url="https://x/lo", priority_score=1.0))
    claimed = await w.claim_next(run_id=1, exclude_node_keys={"hi"})
    assert claimed.node_key == "lo"
    await _close(eng, w, task)


async def test_claim_next_returns_none_when_nothing_claimable(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="d", type=NodeType.detail_url, url="https://x/d"))
    await w.mark_node(nid, NodeStatus.done)
    assert await w.claim_next(run_id=1) is None
    await _close(eng, w, task)


async def test_claim_skips_nodes_at_max_attempts(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="x", type=NodeType.detail_url, url="https://x/x", max_attempts=1))
    first = await w.claim_next(run_id=1)        # attempt_count 0 -> 1
    assert first is not None
    # put it back to retry; now attempt_count(1) >= max_attempts(1) → not claimable
    await w.mark_node(first.id, NodeStatus.retry)
    assert await w.claim_next(run_id=1) is None
    await _close(eng, w, task)


async def test_command_exception_propagates_via_future_and_worker_survives(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    # add_edge to non-existent nodes violates the FK → exception on that Future only.
    with pytest.raises(Exception):
        await w.add_edge(999, 998, EdgeType.pagination_of)
    # worker still alive: a valid command afterwards succeeds.
    nid = await w.upsert_node(NodeSpec(node_key="ok", type=NodeType.detail_url, url="https://x/ok"))
    assert isinstance(nid, int)
    await _close(eng, w, task)
