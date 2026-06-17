import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select

from dext.bridge.fetcher import HumanFetcherBridge
from dext.engine import CrawlEngine
from dext.engine.seeds import node_spec
from dext.seed import UniversitySeed
from dext.storage.db import StorageHandle, create_all, create_engine_for_path, make_session_factory
from dext.storage.lifecycle import open_fresh, open_resume
from dext.storage.models import (
    CrawlRun,
    GraphNode,
    NodeStatus,
    NodeType,
    OrgUnit,
    PageCache,
    Professor,
    ProfessorAffiliation,
    UniversityMeta,
)
from dext.storage.writer import DBWriter, OrgUnitSpec, PageCachePayload
from dext.types import FetchResult


class CountingBridge:
    """Fake bridge: serves canned HTML by identity_url, asserts single in-flight fetch."""

    def __init__(self, pages, *, fetch_delay=0.0):
        self.pages = pages
        self.fetch_delay = fetch_delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.fetch_count = 0

    async def fetch(self, *, url, identity_url, action, context):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            assert self.in_flight == 1, f"single in-flight violated: {self.in_flight}"
            if self.fetch_delay:
                await asyncio.sleep(self.fetch_delay)
            self.fetch_count += 1
            html = self.pages.get(identity_url, "<html><body></body></html>")
            return FetchResult(
                identity_url=identity_url, requested_url=url, final_url=identity_url,
                status_code=200, html=html, title="", pagination_states=[], block_reason=None,
            )
        finally:
            self.in_flight -= 1


class FakeDecider:
    """Swapped in for handlers.decide_links. The parent page yields N pagination links;
    pagination pages are leaves. Optional delays expose decider concurrency / DONE timing."""

    def __init__(self, parent_url, pagination_urls, *, child_delay=0.0, parent_delay=0.0):
        self.parent_url = parent_url
        self.pagination_urls = pagination_urls
        self.child_delay = child_delay
        self.parent_delay = parent_delay
        self.now = 0
        self.max_concurrent = 0

    async def __call__(self, snapshot, candidates, node, context, *, client):
        if snapshot.url == self.parent_url:
            if self.parent_delay:
                await asyncio.sleep(self.parent_delay)
            return SimpleNamespace(
                links=[SimpleNamespace(url=u, label="pagination", confidence=0.9,
                                       is_leaf=False, exclusion_reason=None)
                       for u in self.pagination_urls],
                parse_error=None,
            )
        self.now += 1
        self.max_concurrent = max(self.max_concurrent, self.now)
        try:
            if self.child_delay:
                await asyncio.sleep(self.child_delay)
            return SimpleNamespace(links=[], parse_error=None)
        finally:
            self.now -= 1


async def _storage(tmp_path):
    eng = create_engine_for_path(tmp_path / "driver.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    writer = DBWriter(sf)
    handle = StorageHandle(eng, writer, sf)
    handle.start_writer()
    async with sf() as s:
        s.add(UniversityMeta(name="测试大学", abbr="test"))
        s.add(CrawlRun(id=1, mode="test", status="running"))
        await s.commit()
    return handle


async def _close(handle):
    await handle.close()


def _settings(**overrides):
    base = dict(
        max_attempts=3,
        max_depth=4,
        followup_page_limit=36,
        llm_workers=1,
        decision_workers=1,
        extract_workers=1,
        invalid_json_max_retry=2,
        fetch_timeout_seconds=60,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_driver_fetch_failure_saves_cache_and_does_not_double_increment_attempt(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    bridge = HumanFetcherBridge(settings)
    node_id = await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/szdw.htm", settings=settings, run_id=1)
    )

    async def script():
        job = None
        for _ in range(1000):
            job = bridge.next_job()
            if job is not None:
                break
            await asyncio.sleep(0.01)
        assert job is not None
        assert bridge.next_job() is None
        bridge.fail(job.id, "timeout")

    engine = CrawlEngine(storage, bridge, llm_client=None, settings=settings, run_id=1, university_name="测试大学")
    script_task = asyncio.create_task(script())
    summary = await engine.run()
    await script_task

    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        cache = (await s.execute(select(PageCache).where(PageCache.url == "https://x.edu.cn/szdw.htm"))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.attempt_count == 1
        assert node.last_error == "timeout"
        assert cache.block_reason == "timeout"
    assert summary.status == "failed"
    assert summary.fetch_failed == 1
    await _close(storage)


async def test_driver_skips_terminal_unavailable_completed_page(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    bridge = HumanFetcherBridge(settings)
    node_id = await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/missing.htm", settings=settings, run_id=1)
    )

    async def script():
        job = None
        for _ in range(1000):
            job = bridge.next_job()
            if job is not None:
                break
            await asyncio.sleep(0.01)
        assert job is not None
        bridge.complete(
            job.id,
            html="<html><head><title>404</title></head><body>404 Not Found</body></html>",
            final_url="https://x.edu.cn/missing.htm",
            title="404",
            pagination_states=[],
        )

    engine = CrawlEngine(storage, bridge, llm_client=None, settings=settings, run_id=1, university_name="测试大学")
    script_task = asyncio.create_task(script())
    summary = await engine.run()
    await script_task

    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        cache = (await s.execute(select(PageCache).where(PageCache.url == "https://x.edu.cn/missing.htm"))).scalar_one()
        assert node.status == NodeStatus.skipped
        assert node.last_error == "terminal_unavailable:not_found"
        assert cache.block_reason == "terminal_unavailable:not_found"
    assert summary.status == "completed"
    assert summary.fetch_failed == 1
    await _close(storage)


async def test_driver_replays_from_cache_without_refetching(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    url = "https://x.edu.cn/szdw.htm"
    bridge = CountingBridge({})
    node_id = await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=url, settings=settings, run_id=1)
    )

    await storage.writer.save_page_cache(
        PageCachePayload(
            url=url,
            final_url=url,
            status_code=200,
            html_snapshot="<html><body>师资队伍</body></html>",
            text_snapshot="师资队伍",
            links=[],
            link_signals=[],
            title="师资",
            content_hash="h",
        )
    )

    import dext.engine.handlers as handlers_mod

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], parse_error=None, page_exclusion_reason=None)

    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        engine = CrawlEngine(storage, bridge, llm_client=None, settings=settings, run_id=1, university_name="测试大学")
        summary = await engine.run()
    finally:
        handlers_mod.decide_links = orig

    assert bridge.fetch_count == 0
    assert summary.fetched == 0
    assert summary.status == "completed"
    await _close(storage)


async def test_driver_extracts_cached_detail_after_resume_reset(tmp_path, monkeypatch):
    import dext.engine.workers as workers_mod
    from dext.llm.extractor import ExtractionResult
    from dext.types import ProfessorPayload

    settings = _settings(data_dir=tmp_path / "universities")
    university = UniversitySeed(
        name="复旦大学",
        url="https://www.fudan.edu.cn/",
        org_unit_listing_urls=["https://www.fudan.edu.cn/schools.htm"],
    )
    storage = await open_fresh(university, "fudan", settings)
    first_run_id = await storage.writer.start_run(mode="fresh", settings={}, backup_path=None)
    org_id = await storage.writer.upsert_org_unit(OrgUnitSpec(name="计算与智能创新学院", url="https://ai.fudan.edu.cn"))
    url = "http://ai.fudan.edu.cn/xy_37635/list.htm"
    node_id = await storage.writer.upsert_node(
        node_spec(
            NodeType.detail_url,
            url=url,
            settings=settings,
            run_id=first_run_id,
            org_unit_id=org_id,
            org_unit_name="计算与智能创新学院",
        )
    )
    await storage.writer.save_page_cache(
        PageCachePayload(
            url=url,
            final_url=url,
            status_code=200,
            html_snapshot="<html><body><h1>熊贇</h1><p>职称：教授</p><p>邮件：yunx@fudan.edu.cn</p></body></html>",
            text_snapshot="熊贇 职称：教授 邮件：yunx@fudan.edu.cn",
            links=[],
            link_signals=[],
            title="中文信息",
            content_hash="cached-hash",
        )
    )
    await storage.writer.mark_node(node_id, NodeStatus.in_progress)
    await storage.close()

    storage = await open_resume(university, "fudan", settings)
    resume_run_id = await storage.writer.start_run(mode="resume", settings={}, backup_path=None)
    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert node.status == NodeStatus.retry

    async def _fake_extract(*args, **kwargs):
        return ExtractionResult(
            payloads=[ProfessorPayload(name="熊贇", title="教授", email="yunx@fudan.edu.cn")],
            failure_type=None,
            raw_preview="{...}",
        )

    monkeypatch.setattr(workers_mod, "extract_professors", _fake_extract)
    bridge = CountingBridge({})
    summary = await CrawlEngine(
        storage,
        bridge,
        llm_client=None,
        settings=settings,
        run_id=resume_run_id,
        university_name="复旦大学",
    ).run()

    assert bridge.fetch_count == 0
    assert summary.status == "completed"
    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        prof = (await s.execute(select(Professor))).scalar_one()
        assert node.status == NodeStatus.done
        assert prof.name == "熊贇"
        assert prof.org_unit_name == "计算与智能创新学院"
        assert prof.homepage == url
    await _close(storage)


async def test_targeted_summary_counts_only_selected_org_units(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    org1 = await storage.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x/math"))
    org2 = await storage.writer.upsert_org_unit(OrgUnitSpec(name="物理学院", url="https://x/physics"))
    await storage.writer.upsert_node(
        node_spec(
            NodeType.detail_url,
            url="https://x/math/1",
            settings=settings,
            run_id=1,
            org_unit_id=org1,
            org_unit_name="数学学院",
            status=NodeStatus.done,
        )
    )
    await storage.writer.upsert_node(
        node_spec(
            NodeType.detail_url,
            url="https://x/physics/1",
            settings=settings,
            run_id=1,
            org_unit_id=org2,
            org_unit_name="物理学院",
            status=NodeStatus.failed,
        )
    )
    async with storage.session() as s:
        math_prof = Professor(name="张三", org_unit_name="数学学院")
        physics_prof = Professor(name="李四", org_unit_name="物理学院")
        shared_prof = Professor(name="王五", org_unit_name="数学学院")
        s.add_all([math_prof, physics_prof, shared_prof])
        await s.flush()
        s.add_all(
            [
                ProfessorAffiliation(professor_id=math_prof.id, org_unit_id=org1),
                ProfessorAffiliation(professor_id=physics_prof.id, org_unit_id=org2),
                ProfessorAffiliation(professor_id=shared_prof.id, org_unit_id=org1),
                ProfessorAffiliation(professor_id=shared_prof.id, org_unit_id=org2),
            ]
        )
        await s.commit()

    engine = CrawlEngine(
        storage,
        bridge=None,
        llm_client=None,
        settings=settings,
        run_id=1,
        university_name="测试大学",
        org_unit_ids={org1},
    )
    summary = await engine._build_summary()

    assert summary.status == "completed"
    assert summary.node_status_counts == {NodeStatus.done.value: 1}
    assert summary.professors == 2
    await _close(storage)


async def test_summary_fails_when_selected_org_unit_has_pending_exhausted_node(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    org_id = await storage.writer.upsert_org_unit(OrgUnitSpec(name="管理学院", url="https://x/management"))
    await storage.writer.upsert_node(
        node_spec(
            NodeType.org_unit,
            url="https://x/management",
            settings=settings,
            run_id=1,
            org_unit_id=org_id,
            org_unit_name="管理学院",
            status=NodeStatus.pending,
        )
    )
    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.org_unit_id == org_id))).scalar_one()
        node.attempt_count = node.max_attempts
        await s.commit()

    engine = CrawlEngine(
        storage,
        bridge=None,
        llm_client=None,
        settings=settings,
        run_id=1,
        university_name="测试大学",
        org_unit_ids={org_id},
    )
    summary = await engine._build_summary()

    assert summary.status == "failed"
    assert summary.pending == 1
    assert summary.node_status_counts == {NodeStatus.pending.value: 1}
    await _close(storage)


async def test_summary_fails_when_nodes_are_not_terminal(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    await storage.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x/pending",
            settings=settings,
            run_id=1,
            status=NodeStatus.pending,
        )
    )
    await storage.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x/in-progress",
            settings=settings,
            run_id=1,
            status=NodeStatus.in_progress,
        )
    )
    await storage.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x/skipped",
            settings=settings,
            run_id=1,
            status=NodeStatus.skipped,
        )
    )

    engine = CrawlEngine(storage, bridge=None, llm_client=None, settings=settings, run_id=1, university_name="测试大学")
    summary = await engine._build_summary()

    assert summary.status == "failed"
    assert summary.pending == 1
    assert summary.in_progress == 1
    assert summary.skipped == 1
    await _close(storage)


async def test_decider_runs_concurrently_off_the_fetch_loop(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=6, decision_workers=3, extract_workers=3)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>该页没有更多</body></html>"
    bridge = CountingBridge(pages)
    decider = FakeDecider(parent, pagination_urls, child_delay=0.05)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        engine = CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                             run_id=1, university_name="测试大学")
        summary = await engine.run()
    finally:
        handlers_mod.decide_links = orig

    assert decider.max_concurrent >= 2     # deciders overlapped → off the fetch loop
    assert bridge.max_in_flight == 1       # still single in-flight fetch
    assert bridge.fetch_count == 4         # parent + 3 pagination pages all fetched
    assert summary.status == "completed"
    async with storage.session() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        assert len(nodes) == 4
        assert all(n.status == NodeStatus.done for n in nodes)
    await _close(storage)


async def test_single_fetch_in_flight_across_sibling_nodes(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=6, decision_workers=3, extract_workers=3)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>末页</body></html>"
    bridge = CountingBridge(pages, fetch_delay=0.01)
    decider = FakeDecider(parent, pagination_urls, child_delay=0.03)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    assert bridge.max_in_flight == 1
    assert bridge.fetch_count == 4
    assert summary.status == "completed"
    await _close(storage)


async def test_engine_does_not_terminate_while_decider_in_flight(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=6, decision_workers=3, extract_workers=3)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>末页</body></html>"
    bridge = CountingBridge(pages)
    decider = FakeDecider(parent, pagination_urls, parent_delay=0.05)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    assert summary.status == "completed"
    assert bridge.fetch_count == 4
    async with storage.session() as s:
        pag = (await s.execute(
            select(GraphNode).where(GraphNode.type == NodeType.pagination_url)
        )).scalars().all()
        assert len(pag) == 3                                  # children materialized post-decide
        assert all(n.status == NodeStatus.done for n in pag)  # and fully processed
    await _close(storage)


async def test_extract_pool_saves_result_while_decider_pool_is_busy(tmp_path, monkeypatch):
    import dext.engine.handlers as handlers_mod
    import dext.engine.workers as workers_mod
    from dext.llm.extractor import ExtractionResult
    from dext.types import ProfessorPayload

    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=6, decision_workers=1, extract_workers=1)
    slow_list = "https://x.edu.cn/szdw.htm"
    detail = "https://x.edu.cn/t/zhang.htm"
    bridge = CountingBridge(
        {
            slow_list: "<html><body>师资</body></html>",
            detail: "<html><body>张三 教授</body></html>",
        }
    )
    decision_started = asyncio.Event()
    allow_decision_finish = asyncio.Event()
    professor_saved = asyncio.Event()

    async def _slow_decide(*args, **kwargs):
        decision_started.set()
        await allow_decision_finish.wait()
        return SimpleNamespace(links=[], parse_error=None)

    async def _fake_extract(*args, **kwargs):
        return ExtractionResult(
            payloads=[ProfessorPayload(name="张三", title="教授")],
            failure_type=None,
            raw_preview="{...}",
        )

    org_id = await storage.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=slow_list, settings=settings, run_id=1,
                  org_unit_id=org_id, org_unit_name="数学学院")
    )
    await storage.writer.upsert_node(
        node_spec(NodeType.detail_url, url=detail, settings=settings, run_id=1,
                  org_unit_id=org_id, org_unit_name="数学学院")
    )

    orig_decide = handlers_mod.decide_links
    handlers_mod.decide_links = _slow_decide
    monkeypatch.setattr(workers_mod, "extract_professors", _fake_extract)

    async def wait_for_professor():
        for _ in range(100):
            async with storage.session() as s:
                count = (await s.execute(select(func.count()).select_from(Professor))).scalar_one()
            if count:
                professor_saved.set()
                return
            await asyncio.sleep(0.01)
        raise AssertionError("extract result was not saved while decider was busy")

    try:
        engine_task = asyncio.create_task(
            CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                        run_id=1, university_name="测试大学").run()
        )
        await asyncio.wait_for(decision_started.wait(), timeout=1)
        await asyncio.wait_for(wait_for_professor(), timeout=2)
        assert professor_saved.is_set()
        allow_decision_finish.set()
        summary = await asyncio.wait_for(engine_task, timeout=2)
    finally:
        handlers_mod.decide_links = orig_decide

    assert bridge.max_in_flight == 1
    assert summary.status == "completed"
    async with storage.session() as s:
        prof = (await s.execute(select(Professor))).scalar_one()
        assert prof.name == "张三"
    await _close(storage)


async def test_decider_parse_error_marks_node_retry_via_worker(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    url = "https://x.edu.cn/szdw.htm"
    bridge = CountingBridge({url: "<html><body>师资</body></html>"})

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], parse_error=True)

    node_id = await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=url, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.last_error == "decider_invalid_json"
    assert summary.status == "failed"
    await _close(storage)


async def test_async_decider_end_to_end_live(tmp_path, live_settings):
    from dext.llm.client import LLMClient

    storage = await _storage(tmp_path)
    settings = live_settings
    listing = "https://x.edu.cn/yx.htm"
    org = "https://x.edu.cn/math/index.htm"
    flist = "https://x.edu.cn/math/szdw.htm"
    d1 = "https://x.edu.cn/math/t/zhang.htm"
    d2 = "https://x.edu.cn/math/t/li.htm"
    pages = {
        listing: '<html><body><h2>院系设置</h2>'
                 '<a href="https://x.edu.cn/math/index.htm">数学学院</a></body></html>',
        org: '<html><body><h2>数学学院</h2>'
             '<a href="https://x.edu.cn/math/szdw.htm">师资队伍</a></body></html>',
        flist: '<html><body><h2>师资队伍</h2>'
               '<a href="https://x.edu.cn/math/t/zhang.htm">张三 教授</a>'
               '<a href="https://x.edu.cn/math/t/li.htm">李四 副教授</a></body></html>',
        d1: '<html><body><h1>张三</h1><p>职称：教授</p>'
            '<p>邮箱：zhang@x.edu.cn</p><p>研究方向：代数几何</p></body></html>',
        d2: '<html><body><h1>李四</h1><p>职称：副教授</p>'
            '<p>邮箱：li@x.edu.cn</p><p>研究方向：拓扑学</p></body></html>',
    }
    bridge = CountingBridge(pages)

    await storage.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url=listing, settings=settings, run_id=1)
    )
    engine = CrawlEngine(storage, bridge, llm_client=LLMClient(settings), settings=settings,
                         run_id=1, university_name="测试大学")
    summary = await engine.run()

    assert bridge.max_in_flight == 1
    async with storage.session() as s:
        profs = (await s.execute(select(Professor))).scalars().all()
        affils = (await s.execute(select(ProfessorAffiliation))).scalars().all()
        assert len(profs) >= 1
        assert len(affils) >= 1
    await _close(storage)
