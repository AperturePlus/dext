import asyncio
from types import SimpleNamespace

from sqlalchemy import select

import dext.engine.workers as workers_mod
from dext.engine.workers import ExtractTask, _payloads_with_system_homepage, process_extract_task
from dext.engine.seeds import node_spec
from dext.llm.extractor import ExtractionResult
from dext.page.links import build_snapshot
from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import GraphNode, NodeStatus, NodeType
from dext.storage.writer import DBWriter, NodeSpec, OrgUnitSpec
from dext.types import ProfessorPayload


def test_system_homepage_fills_missing_payload_homepage():
    payloads = [ProfessorPayload(name="张三", external_link="https://scholar.google.com/citations?user=x")]

    out = _payloads_with_system_homepage(payloads, "https://x.edu.cn/teacher/zhang.htm")

    assert out[0].homepage == "https://x.edu.cn/teacher/zhang.htm"
    assert out[0].external_link == "https://scholar.google.com/citations?user=x"


def test_system_homepage_overrides_llm_payload_homepage():
    payloads = [
        ProfessorPayload(
            name="李四",
            homepage="https://wrong.example.com/li",
            external_link="https://li.example.com/",
        )
    ]

    out = _payloads_with_system_homepage(payloads, "https://x.edu.cn/teacher/li.htm")

    assert out[0].homepage == "https://x.edu.cn/teacher/li.htm"
    assert out[0].external_link == "https://li.example.com/"
    assert payloads[0].homepage == "https://wrong.example.com/li"


async def _storage(tmp_path):
    eng = create_engine_for_path(tmp_path / "workers.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    return SimpleNamespace(engine=eng, session_factory=sf, writer=w, task=task)


async def _close(h):
    await h.writer.stop()
    await h.task
    await h.engine.dispose()


def _settings():
    return SimpleNamespace(max_attempts=3, max_depth=4, followup_page_limit=36, invalid_json_max_retry=2)


async def test_extract_skips_llm_when_same_url_already_done(tmp_path, monkeypatch):
    # Part A regression guard: a duplicate detail_url node (same URL as an already
    # `done` node) must NOT call the LLM — it reuses the prior extraction. This is
    # the runtime safety net for the node_key-dedup regression that caused mass
    # token waste (commit 210a66a changed the key format with no data migration).
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    url = "https://x.edu.cn/t/zhang.htm"
    done_id = await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url=url, settings=_settings(), run_id=1,
                  org_unit_id=org_id, org_unit_name="数学学院")
    )
    await h.writer.mark_node(done_id, NodeStatus.done)
    # A second node for the SAME url but a different node_key (simulating the
    # legacy-vs-new key collision that the migration misses at runtime).
    dup_id = await h.writer.upsert_node(
        NodeSpec(node_key="legacy:url:https://x.edu.cn/t/zhang.htm", type=NodeType.detail_url,
                 url=url, org_unit_id=org_id, org_unit_name="数学学院")
    )
    snap = build_snapshot("<html><body>张三 教授</body></html>", url, url, "张三")

    async def _must_not_call(*args, **kwargs):
        raise AssertionError("LLM extract_professors must not be called for a duplicate-url node")

    monkeypatch.setattr(workers_mod, "extract_professors", _must_not_call)

    task = ExtractTask(node_id=dup_id, node_key="legacy", snapshot=snap,
                       org_unit_id=org_id, org_unit_name="数学学院", attempt_count=1)
    await process_extract_task(task, h, llm_client=None, settings=_settings())

    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == dup_id))).scalar_one()
        assert node.status == NodeStatus.done
        assert node.last_error == f"duplicate_url_reused:{done_id}"
    await _close(h)


async def test_extract_skips_llm_for_cross_scheme_done_twin(tmp_path, monkeypatch):
    # The second waste class: a page discovered as http:// (extracted, done) and
    # re-discovered as https:// (pending). The runtime guard must treat them as the
    # same page (scheme-insensitive) and skip the LLM call. 627 URLs hit this in
    # fudan.db → 225 redundant re-extractions of already-saved professors.
    h = await _storage(tmp_path)
    http_url = "http://phys.example.edu.cn/p1.htm"
    https_url = "https://phys.example.edu.cn/p1.htm"
    done_id = await h.writer.upsert_node(
        NodeSpec(node_key=f"url:{http_url}", type=NodeType.detail_url, url=http_url,
                 org_unit_name="物理学院")
    )
    await h.writer.mark_node(done_id, NodeStatus.done)
    dup_id = await h.writer.upsert_node(
        NodeSpec(node_key=f"url:{https_url}", type=NodeType.detail_url, url=https_url,
                 org_unit_name="物理学院")
    )
    snap = build_snapshot("<html><body>彭瑞 教授</body></html>", https_url, https_url, "彭瑞")

    async def _must_not_call(*args, **kwargs):
        raise AssertionError("LLM must not be called for a cross-scheme duplicate")

    monkeypatch.setattr(workers_mod, "extract_professors", _must_not_call)

    task = ExtractTask(node_id=dup_id, node_key=f"url:{https_url}", snapshot=snap,
                       org_unit_id=None, org_unit_name="物理学院", attempt_count=1)
    await process_extract_task(task, h, llm_client=None, settings=_settings())

    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == dup_id))).scalar_one()
        assert node.status == NodeStatus.done
        assert node.last_error == f"duplicate_url_reused:{done_id}"
    await _close(h)


async def test_excluded_extraction_skips_node(tmp_path, monkeypatch):
    h = await _storage(tmp_path)
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/szdw/admin.htm",
                  settings=_settings(), run_id=1, org_unit_id=None, org_unit_name="某学院")
    )
    snap = build_snapshot("<html><body>行政团队</body></html>",
                          "https://x.edu.cn/szdw/admin.htm", "https://x.edu.cn/szdw/admin.htm", "专职行政")

    async def _fake_extract(*args, **kwargs):
        return ExtractionResult(payloads=[], failure_type="excluded", exclusion_reason="administration")

    monkeypatch.setattr(workers_mod, "extract_professors", _fake_extract)

    task = ExtractTask(node_id=node_id, node_key="n", snapshot=snap,
                       org_unit_id=None, org_unit_name="某学院", attempt_count=1)
    await process_extract_task(task, h, llm_client=None, settings=_settings())

    async with h.session_factory() as s:
        row = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert row.status == NodeStatus.skipped
        assert row.last_error == "excluded:administration"
    await _close(h)


async def test_decision_worker_routes_decide_task_to_dispatch(tmp_path):
    import dext.engine.handlers as handlers_mod
    from dext.engine.handlers import HandlerDeps
    from dext.engine.workers import DecideTask, InFlightTracker, decision_worker
    from dext.storage.models import OrgUnit
    from dext.storage.writer import ClaimedNode

    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm",
                  settings=_settings(), run_id=1)
    )
    snap = build_snapshot("<html><body>院系设置</body></html>",
                          "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[SimpleNamespace(
            url="https://x.edu.cn/math.htm", label="college", confidence=0.9,
            is_leaf=False, org_unit_name="数学学院", exclusion_reason=None)])

    node = ClaimedNode(id=listing_id, node_key="listing", type=NodeType.org_listing_url,
                       url=snap.url, org_unit_id=None, org_unit_name=None, depth=0,
                       attempt_count=1, priority_score=100, content_hash=None, metadata=None)
    q = asyncio.Queue()
    tracker = InFlightTracker()

    def deps_factory(task):
        return HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=q,
                           reported_pagination_states=task.reported_pagination_states,
                           raw_html=task.raw_html, decision_center=None)

    await q.put(DecideTask(node=node, snapshot=snap, raw_html="<html></html>",
                           reported_pagination_states=[]))
    await q.put(None)
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        await decision_worker("decision-0", q, tracker, deps_factory=deps_factory)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        listing = (await s.execute(select(GraphNode).where(GraphNode.id == listing_id))).scalar_one()
        org_node = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.org_unit))).scalar_one()
        org = (await s.execute(select(OrgUnit))).scalar_one()
        assert listing.status == NodeStatus.done
        assert org_node.org_unit_name == "数学学院"
        assert org.name == "数学学院"
    assert tracker.in_flight == 0
    await _close(h)


async def test_extract_worker_processes_extract_task(tmp_path, monkeypatch):
    from dext.engine.workers import InFlightTracker, extract_worker
    from dext.storage.models import Professor

    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/t/zhang.htm",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="数学学院")
    )
    snap = build_snapshot("<html><body>张三 教授</body></html>",
                          "https://x.edu.cn/t/zhang.htm", "https://x.edu.cn/t/zhang.htm", "张三")

    async def _fake_extract(*args, **kwargs):
        return ExtractionResult(
            payloads=[ProfessorPayload(name="张三", title="教授")],
            failure_type=None,
            raw_preview="{...}",
        )

    monkeypatch.setattr(workers_mod, "extract_professors", _fake_extract)

    q = asyncio.Queue()
    tracker = InFlightTracker()
    await q.put(ExtractTask(node_id=node_id, node_key="n", snapshot=snap,
                            org_unit_id=org_id, org_unit_name="数学学院", attempt_count=1))
    await q.put(None)
    await extract_worker("extract-0", q, h, None, _settings(), tracker)

    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        prof = (await s.execute(select(Professor))).scalar_one()
        assert node.status == NodeStatus.done
        assert prof.name == "张三"
    assert tracker.in_flight == 0
    await _close(h)


async def test_split_workers_reject_wrong_task_type(tmp_path):
    from dext.engine.handlers import HandlerDeps
    from dext.engine.workers import DecideTask, InFlightTracker, decision_worker, extract_worker
    from dext.storage.writer import ClaimedNode

    h = await _storage(tmp_path)
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/t/zhang.htm",
                  settings=_settings(), run_id=1)
    )
    snap = build_snapshot("<html><body>张三</body></html>",
                          "https://x.edu.cn/t/zhang.htm", "https://x.edu.cn/t/zhang.htm", "张三")

    extract_q = asyncio.Queue()
    extract_tracker = InFlightTracker()
    await extract_q.put(DecideTask(
        node=ClaimedNode(id=node_id, node_key="n", type=NodeType.detail_url, url=snap.url,
                         org_unit_id=None, org_unit_name=None, depth=0, attempt_count=1,
                         priority_score=0, content_hash=None, metadata=None),
        snapshot=snap,
        raw_html="<html></html>",
        reported_pagination_states=[],
    ))
    await extract_q.put(None)
    try:
        await extract_worker("extract-0", extract_q, h, None, _settings(), extract_tracker)
        raise AssertionError("extract_worker accepted DecideTask")
    except TypeError as exc:
        assert "expected ExtractTask" in str(exc)
    assert extract_tracker.in_flight == 0

    decision_q = asyncio.Queue()
    decision_tracker = InFlightTracker()
    await decision_q.put(ExtractTask(node_id=node_id, node_key="n", snapshot=snap,
                                     org_unit_id=None, org_unit_name="", attempt_count=1))
    await decision_q.put(None)

    def deps_factory(task):
        return HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue())

    try:
        await decision_worker("decision-0", decision_q, decision_tracker, deps_factory=deps_factory)
        raise AssertionError("decision_worker accepted ExtractTask")
    except TypeError as exc:
        assert "expected DecideTask" in str(exc)
    assert decision_tracker.in_flight == 0
    await _close(h)
