import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from dext.bridge.fetcher import HumanFetcherBridge
from dext.engine import CrawlEngine
from dext.engine.seeds import node_spec
from dext.llm import LLMClient
from dext.storage.db import StorageHandle, create_all, create_engine_for_path, make_session_factory
from dext.storage.models import CrawlRun, GraphNode, NodeStatus, NodeType, Professor, UniversityMeta
from dext.storage.writer import DBWriter, OrgUnitSpec

_LIST_HTML = """
<html><head><title>师资队伍</title></head><body>
<h1>数学学院 师资队伍</h1>
<ul class="teacher-list">
  <li><a href="/teacher/info/1001.htm">张三 教授</a></li>
</ul>
</body></html>
"""

_DETAIL_HTML = """
<html><head><title>张三 - 数学学院</title></head><body>
<h1>张三 教授</h1>
<p>职称：教授，博士生导师</p>
<p>研究方向：人工智能、机器学习</p>
<p>邮箱：zhangsan@x.edu.cn</p>
<p>个人简介：张三，2005年博士毕业，主要从事人工智能与机器学习研究。</p>
<p>教育经历：2000年至2005年在测试大学学习并获得博士学位。</p>
<p>科研项目：主持多项人工智能相关科研项目。</p>
</body></html>
"""


async def _storage(tmp_path):
    eng = create_engine_for_path(tmp_path / "engine-live.db")
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


def _settings(live_settings):
    data = live_settings.model_dump()
    data.update(
        max_depth=3,
        decision_workers=1,
        extract_workers=1,
        followup_page_limit=12,
        fetch_timeout_seconds=60,
    )
    return SimpleNamespace(**data)


async def _complete_jobs(bridge, expected):
    seen = []
    for _ in range(expected):
        job = None
        for _ in range(1000):
            job = bridge.next_job()
            if job is not None:
                break
            await asyncio.sleep(0.01)
        assert job is not None
        seen.append(job.url)
        if job.url.endswith("/szdw.htm"):
            bridge.complete(
                job.id,
                html=_LIST_HTML,
                final_url="https://x.edu.cn/szdw.htm",
                title="师资队伍",
                pagination_states=[],
            )
        elif job.url.endswith("/teacher/info/1001.htm"):
            bridge.complete(
                job.id,
                html=_DETAIL_HTML,
                final_url="https://x.edu.cn/teacher/info/1001.htm",
                title="张三 - 数学学院",
                pagination_states=[],
            )
        else:
            pytest.fail(f"unexpected fetch URL: {job.url}")
    return seen


async def test_engine_live_bridge_to_llm_to_professor(tmp_path, live_settings):
    storage = await _storage(tmp_path)
    settings = _settings(live_settings)
    bridge = HumanFetcherBridge(settings)
    org_id = await storage.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    await storage.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x.edu.cn/szdw.htm",
            settings=settings,
            run_id=1,
            org_unit_id=org_id,
            org_unit_name="数学学院",
        )
    )

    engine = CrawlEngine(storage, bridge, LLMClient(settings), settings, run_id=1, university_name="测试大学")
    script_task = asyncio.create_task(_complete_jobs(bridge, expected=2))
    summary = await engine.run()
    seen = await script_task

    async with storage.session() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        professors = (await s.execute(select(Professor))).scalars().all()
        assert seen == ["https://x.edu.cn/szdw.htm", "https://x.edu.cn/teacher/info/1001.htm"]
        assert any(n.type == NodeType.detail_url and n.status == NodeStatus.done for n in nodes)
        assert len(professors) >= 1
    assert summary.status == "completed"
    assert summary.professors >= 1
    await _close(storage)
