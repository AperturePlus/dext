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


async def test_upsert_node_dedups_detail_url_across_org_units(tmp_path):
    # Regression for the node_key scheme change (commit 210a66a): the same detail
    # URL discovered under two org_units must collapse to a single graph node, so it
    # is extracted once — not once per org_unit (which caused mass token waste).
    from dext.engine.seeds import node_spec
    from dext.config import Settings

    eng, sf, w, task = await _writer(tmp_path)
    settings = Settings()
    org_a = await w.upsert_org_unit(OrgUnitSpec(name="学院A", url="https://x/a"))
    org_b = await w.upsert_org_unit(OrgUnitSpec(name="学院B", url="https://x/b"))
    url = "https://x.edu.cn/t/zhang.htm"
    id_a = await w.upsert_node(
        node_spec(NodeType.detail_url, url=url, settings=settings, run_id=1,
                  org_unit_id=org_a, org_unit_name="学院A", subtree=True)
    )
    id_b = await w.upsert_node(
        node_spec(NodeType.detail_url, url=url, settings=settings, run_id=1,
                  org_unit_id=org_b, org_unit_name="学院B", subtree=True)
    )
    assert id_a == id_b  # same node — dedup by canonical URL
    async with sf() as s:
        rows = (await s.execute(select(GraphNode).where(GraphNode.url == url))).scalars().all()
        assert len(rows) == 1
    await _close(eng, w, task)


async def test_find_done_detail_node_for_url_skips_self_and_matches_other(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    url = "https://x/dup"
    a = await w.upsert_node(NodeSpec(node_key="url:https://x/dup", type=NodeType.detail_url, url=url))
    b = await w.upsert_node(NodeSpec(node_key="legacy:https://x/dup", type=NodeType.detail_url, url=url))
    await w.mark_node(a, NodeStatus.done)
    # self-excluded → None; the other done node is found when querying from b
    assert await w.find_done_detail_node_for_url(url, exclude_node_id=a) is None
    assert await w.find_done_detail_node_for_url(url, exclude_node_id=b) == a
    await _close(eng, w, task)


async def test_find_done_detail_node_matches_cross_scheme(tmp_path):
    # A page discovered as http:// and https:// is the SAME page; the done node under
    # one scheme must satisfy the dedup guard for the other scheme's node. This was the
    # core bug: 627 URLs had both http and https detail nodes, causing 225 redundant
    # re-extractions of already-extracted (professors-saved) pages.
    eng, sf, w, task = await _writer(tmp_path)
    http_node = await w.upsert_node(
        NodeSpec(node_key="url:http://x/p", type=NodeType.detail_url, url="http://x/p")
    )
    https_node = await w.upsert_node(
        NodeSpec(node_key="url:https://x/p", type=NodeType.detail_url, url="https://x/p")
    )
    await w.mark_node(http_node, NodeStatus.done)
    # the https twin should find the http done node
    assert await w.find_done_detail_node_for_url("https://x/p", exclude_node_id=https_node) == http_node
    # and symmetrically
    await w.mark_node(https_node, NodeStatus.done)
    await w.mark_node(http_node, NodeStatus.pending)  # reset to test the other direction
    assert await w.find_done_detail_node_for_url("http://x/p", exclude_node_id=http_node) == https_node
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


async def test_claim_next_honors_org_unit_filter(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    org1 = await w.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x/math"))
    org2 = await w.upsert_org_unit(OrgUnitSpec(name="物理学院", url="https://x/physics"))
    await w.upsert_node(
        NodeSpec(
            node_key="org1-low",
            type=NodeType.detail_url,
            url="https://x/org1",
            org_unit_id=org1,
            priority_score=1.0,
        )
    )
    await w.upsert_node(
        NodeSpec(
            node_key="org2-high",
            type=NodeType.detail_url,
            url="https://x/org2",
            org_unit_id=org2,
            priority_score=9.0,
        )
    )

    claimed = await w.claim_next(run_id=1, org_unit_ids={org1})

    assert claimed is not None
    assert claimed.node_key == "org1-low"
    assert claimed.org_unit_id == org1
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


from dext.storage.models import (
    CrawlRun,
    ExtractionAttempt,
    ExtractionFailure,
    OrgUnit,
    PageCache,
    UniversityMeta,
)
from dext.storage.writer import PageCachePayload


async def test_save_page_cache_upserts_by_identity_url(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    url = "https://x?__ycl_page=2"  # identity URL (synthetic for form pagination)
    await w.save_page_cache(PageCachePayload(url=url, title="第2页", status_code=200, links=["a", "b"]))
    await w.save_page_cache(PageCachePayload(url=url, title="第2页改", status_code=200, content_hash="h2"))
    async with sf() as s:
        rows = (await s.execute(select(PageCache).where(PageCache.url == url))).scalars().all()
        assert len(rows) == 1  # upsert, not duplicate
        assert rows[0].title == "第2页改"
        assert rows[0].content_hash == "h2"
        assert rows[0].snapshot_encoding == "utf-8"
    await _close(eng, w, task)


async def test_extraction_attempt_record_then_finish(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="d", type=NodeType.detail_url, url="https://x/d"))
    aid = await w.record_extraction_attempt(graph_node_id=nid, attempt=1, input_cache_url="https://x/d")
    await w.finish_extraction_attempt(aid, status="succeeded", raw_output_preview="{...}")
    async with sf() as s:
        att = (await s.execute(select(ExtractionAttempt).where(ExtractionAttempt.id == aid))).scalar_one()
        assert att.status == "succeeded"
        assert att.finished_at is not None
    await _close(eng, w, task)


async def test_record_extraction_failure(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.record_extraction_failure(failure_type="invalid_json", resolver="dropped",
                                      professor_name_hint="张三", source_url="https://x/d")
    async with sf() as s:
        rows = (await s.execute(select(ExtractionFailure))).scalars().all()
        assert len(rows) == 1 and rows[0].failure_type == "invalid_json"
    await _close(eng, w, task)


async def test_run_lifecycle_and_university_status(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    async with sf() as s:  # a meta row must exist for last_run_id wiring
        s.add(UniversityMeta(name="测试大学", abbr="test"))
        await s.commit()
    rid = await w.start_run(mode="fresh", settings={"max_depth": 4})
    await w.update_university_status("in_progress")
    await w.finish_run(rid, status="completed", summary={"professors": 10})
    async with sf() as s:
        run = (await s.execute(select(CrawlRun).where(CrawlRun.id == rid))).scalar_one()
        meta = (await s.execute(select(UniversityMeta))).scalar_one()
        assert run.status == "completed" and run.finished_at is not None
        assert run.summary_json == {"professors": 10}
        assert meta.crawl_status == "in_progress"
        assert meta.last_run_id == rid
    await _close(eng, w, task)


async def test_update_org_unit_status(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    oid = await w.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x/math", status="pending"))
    await w.update_org_unit_status(oid, "no_faculty_page")
    async with sf() as s:
        row = (await s.execute(select(OrgUnit).where(OrgUnit.id == oid))).scalar_one()
        assert row.status == "no_faculty_page"
    await _close(eng, w, task)


async def test_count_subtree_facet_nodes(tmp_path):
    eng = create_engine_for_path(tmp_path / "count.db")
    await create_all(eng)
    w = DBWriter(make_session_factory(eng))
    task = asyncio.create_task(w.run())
    try:
        org = await w.upsert_org_unit(OrgUnitSpec(name="某院", url="https://x.edu.cn/c/"))
        other = await w.upsert_org_unit(OrgUnitSpec(name="它院", url="https://x.edu.cn/o/"))
        for i, t in enumerate(
            [NodeType.faculty_list_url, NodeType.faculty_followup_url, NodeType.pagination_url, NodeType.detail_url]
        ):
            await w.upsert_node(NodeSpec(node_key=f"k{i}", type=t, url=f"https://x.edu.cn/c/{i}", org_unit_id=org))
        await w.upsert_node(NodeSpec(node_key="ko", type=NodeType.faculty_followup_url,
                                     url="https://x.edu.cn/o/1", org_unit_id=other))
        # org subtree: faculty_list + followup + pagination = 3 (detail_url NOT counted)
        assert await w.count_subtree_facet_nodes(org) == 3
        assert await w.count_subtree_facet_nodes(other) == 1
    finally:
        await w.stop()
        await task
        await eng.dispose()


async def test_reset_org_unit_subtree_deletes_discovered_and_resets_entrypoints(tmp_path):
    # --reset -oid X: detail/followup/pagination nodes + edges + page_cache +
    # extraction attempts are deleted; org_unit + faculty_list_url entry-point
    # nodes are reset to pending; the org_unit row status is reset to pending.
    eng, sf, w, task = await _writer(tmp_path)
    try:
        org = await w.upsert_org_unit(OrgUnitSpec(name="计算机学院", url="https://cse.neu.edu.cn/", status="completed"))
        # entry-point nodes (kept & reset)
        org_node = await w.upsert_node(
            NodeSpec(node_key="org:1", type=NodeType.org_unit, url="https://cse.neu.edu.cn/",
                     org_unit_id=org, org_unit_name="计算机学院")
        )
        faculty_list = await w.upsert_node(
            NodeSpec(node_key="fl:1", type=NodeType.faculty_list_url, url="https://cse.neu.edu.cn/szdw.htm",
                     org_unit_id=org, org_unit_name="计算机学院")
        )
        await w.mark_node(org_node, NodeStatus.done)
        await w.mark_node(faculty_list, NodeStatus.done)
        await w.add_edge(org_node, faculty_list, EdgeType.belongs_to_org_unit)
        # discovered subtree nodes (deleted)
        detail = await w.upsert_node(
            NodeSpec(node_key="d:1", type=NodeType.detail_url, url="https://cse.neu.edu.cn/p1.htm",
                     org_unit_id=org, org_unit_name="计算机学院")
        )
        followup = await w.upsert_node(
            NodeSpec(node_key="f:1", type=NodeType.faculty_followup_url, url="https://cse.neu.edu.cn/f1.htm",
                     org_unit_id=org, org_unit_name="计算机学院")
        )
        await w.mark_node(detail, NodeStatus.skipped, last_error="terminal_unavailable:empty_page")
        await w.mark_node(followup, NodeStatus.done)
        await w.add_edge(faculty_list, detail, EdgeType.detail_candidate_of)
        await w.add_edge(faculty_list, followup, EdgeType.discovered_on_page)
        # page_cache + extraction attempts for the discovered subtree
        await w.save_page_cache(PageCachePayload(url="https://cse.neu.edu.cn/p1.htm", html_snapshot="<x>",
                                                  block_reason="terminal_unavailable:empty_page"))
        await w.save_page_cache(PageCachePayload(url="https://cse.neu.edu.cn/szdw.htm", html_snapshot="<list>"))
        await w.record_extraction_attempt(graph_node_id=detail, attempt=1, input_cache_url="https://cse.neu.edu.cn/p1.htm")

        counts = await w.reset_org_unit_subtree(org)

        assert counts["nodes_deleted"] == 2          # detail + followup
        assert counts["entrypoints_reset"] == 2       # org_unit + faculty_list_url
        assert counts["edges_deleted"] == 2           # the two edges touching detail/followup
        assert counts["caches_deleted"] == 2          # p1 (deleted) + szdw (entry-point reset); f1/org_url had no cache
        async with sf() as s:
            remaining = {n.node_key: n for n in (await s.execute(select(GraphNode).where(GraphNode.org_unit_id == org))).scalars().all()}
            assert set(remaining) == {"org:1", "fl:1"}                       # subtree deleted
            assert remaining["org:1"].status == NodeStatus.pending
            assert remaining["fl:1"].status == NodeStatus.pending
            assert remaining["org:1"].attempt_count == 0
            assert remaining["org:1"].completed_at is None
            assert remaining["org:1"].last_error is None
            assert len((await s.execute(select(GraphEdge))).scalars().all()) == 1   # entrypoint<->entrypoint edge kept; subtree edges gone
            assert len((await s.execute(select(ExtractionAttempt))).scalars().all()) == 0
            assert len((await s.execute(select(PageCache))).scalars().all()) == 0    # all caches cleared
            ou = (await s.execute(select(OrgUnit).where(OrgUnit.id == org))).scalar_one()
            assert ou.status == "pending"
    finally:
        await _close(eng, w, task)


async def test_reset_org_unit_subtree_preserves_other_org_units(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    try:
        a = await w.upsert_org_unit(OrgUnitSpec(name="A院", url="https://x/a"))
        b = await w.upsert_org_unit(OrgUnitSpec(name="B院", url="https://x/b"))
        await w.upsert_node(NodeSpec(node_key="a:d", type=NodeType.detail_url, url="https://x/a/d", org_unit_id=a))
        await w.upsert_node(NodeSpec(node_key="b:d", type=NodeType.detail_url, url="https://x/b/d", org_unit_id=b))
        await w.upsert_node(NodeSpec(node_key="b:fl", type=NodeType.faculty_list_url, url="https://x/b/fl", org_unit_id=b))
        await w.add_edge(
            await w.upsert_node(NodeSpec(node_key="b:org", type=NodeType.org_unit, url="https://x/b", org_unit_id=b)),
            await w.upsert_node(NodeSpec(node_key="b:fl2", type=NodeType.faculty_list_url, url="https://x/b/fl2", org_unit_id=b)),
            EdgeType.belongs_to_org_unit,
        )

        counts = await w.reset_org_unit_subtree(a)

        assert counts["nodes_deleted"] == 1
        async with sf() as s:
            keys = {n.node_key for n in (await s.execute(select(GraphNode))).scalars().all()}
            # only a:d deleted; B's nodes untouched (including its edge)
            assert keys == {"b:d", "b:fl", "b:org", "b:fl2"}
            assert len((await s.execute(select(GraphEdge))).scalars().all()) == 1
    finally:
        await _close(eng, w, task)


async def test_reset_bad_detail_snapshots_resets_empty_and_empty_page_nodes(tmp_path):
    # --reset (no -oid): detail nodes whose page_cache text_snapshot is empty/
    # whitespace or flagged terminal_unavailable:empty_page are reset to pending
    # and their stale page_cache deleted; good detail nodes are left alone.
    eng, sf, w, task = await _writer(tmp_path)
    try:
        # bad: empty text snapshot (the capture regression)
        bad_empty = await w.upsert_node(NodeSpec(node_key="d:empty", type=NodeType.detail_url, url="https://x/empty"))
        await w.mark_node(bad_empty, NodeStatus.skipped, last_error="terminal_unavailable:empty_page",
                          content_hash="h")
        await w.save_page_cache(PageCachePayload(url="https://x/empty", html_snapshot="<html></html>",
                                                  block_reason="terminal_unavailable:empty_page"))
        # bad: whitespace-only text snapshot
        bad_ws = await w.upsert_node(NodeSpec(node_key="d:ws", type=NodeType.detail_url, url="https://x/ws"))
        await w.mark_node(bad_ws, NodeStatus.skipped)
        await w.save_page_cache(PageCachePayload(url="https://x/ws", text_snapshot="   \n\t ",
                                                  html_snapshot="<html></html>"))
        # bad: explicit empty_page block_reason even if text non-empty (defensive)
        bad_flag = await w.upsert_node(NodeSpec(node_key="d:flag", type=NodeType.detail_url, url="https://x/flag"))
        await w.mark_node(bad_flag, NodeStatus.skipped)
        await w.save_page_cache(PageCachePayload(url="https://x/flag", text_snapshot="something",
                                                  block_reason="terminal_unavailable:empty_page"))
        # good: detail node with real text — must NOT be touched
        good = await w.upsert_node(NodeSpec(node_key="d:good", type=NodeType.detail_url, url="https://x/good"))
        await w.mark_node(good, NodeStatus.done, content_hash="good-hash")
        await w.save_page_cache(PageCachePayload(url="https://x/good", text_snapshot="教授张三的详情",
                                                  html_snapshot="<p>张三</p>"))
        # good: non-detail leaf-like node (faculty_list) with empty cache — not in scope
        fl = await w.upsert_node(NodeSpec(node_key="fl:1", type=NodeType.faculty_list_url, url="https://x/fl"))
        await w.mark_node(fl, NodeStatus.skipped)
        await w.save_page_cache(PageCachePayload(url="https://x/fl", html_snapshot="<x>"))

        counts = await w.reset_bad_detail_snapshots()

        assert counts["nodes_reset"] == 3
        async with sf() as s:
            nodes = {n.node_key: n for n in (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalars().all()}
            for key in ("d:empty", "d:ws", "d:flag"):
                assert nodes[key].status == NodeStatus.pending
                assert nodes[key].attempt_count == 0
                assert nodes[key].last_error is None
                assert nodes[key].content_hash is None
                assert nodes[key].completed_at is None
            # good detail node untouched
            assert nodes["d:good"].status == NodeStatus.done
            assert nodes["d:good"].content_hash == "good-hash"
            # stale caches for bad nodes deleted; good caches preserved
            cache_urls = {c.url for c in (await s.execute(select(PageCache))).scalars().all()}
            assert "https://x/empty" not in cache_urls
            assert "https://x/ws" not in cache_urls
            assert "https://x/flag" not in cache_urls
            assert "https://x/good" in cache_urls
            assert "https://x/fl" in cache_urls
    finally:
        await _close(eng, w, task)


async def test_reset_bad_detail_snapshots_noop_when_none_bad(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    try:
        good = await w.upsert_node(NodeSpec(node_key="d:good", type=NodeType.detail_url, url="https://x/good"))
        await w.mark_node(good, NodeStatus.done)
        await w.save_page_cache(PageCachePayload(url="https://x/good", text_snapshot="有内容"))
        counts = await w.reset_bad_detail_snapshots()
        assert counts == {"nodes_reset": 0, "caches_deleted": 0}
    finally:
        await _close(eng, w, task)
