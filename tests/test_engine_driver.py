import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.bridge.fetcher import HumanFetcherBridge
from dext.engine import CrawlEngine
from dext.engine.seeds import node_spec
from dext.storage.db import StorageHandle, create_all, create_engine_for_path, make_session_factory
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
from dext.storage.writer import DBWriter, OrgUnitSpec


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
