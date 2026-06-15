import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.bridge.fetcher import HumanFetcherBridge
from dext.engine import CrawlEngine
from dext.engine.seeds import node_spec
from dext.storage.db import StorageHandle, create_all, create_engine_for_path, make_session_factory
from dext.storage.models import CrawlRun, GraphNode, NodeStatus, NodeType, PageCache, UniversityMeta
from dext.storage.writer import DBWriter


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
