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
from dext.storage.writer import DBWriter
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
