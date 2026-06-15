import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.engine.handlers import (
    HandlerDeps,
    _create_followup_nodes,
    _create_form_pagination_nodes,
    _create_url_pagination_nodes,
    _detail_like_urls,
    fetch_action_from_metadata,
)
from dext.engine.seeds import node_spec
from dext.page.links import build_snapshot
from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import EdgeType, GraphEdge, GraphNode, NodeType
from dext.storage.writer import ClaimedNode, DBWriter, OrgUnitSpec
from dext.types import PaginationState


async def _storage(tmp_path):
    eng = create_engine_for_path(tmp_path / "handlers.db")
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
    return SimpleNamespace(max_attempts=3, max_depth=4, followup_page_limit=36)


async def test_faculty_page_creates_pagination_followup_and_form_nodes(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    parent_id = await h.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x.edu.cn/szdw/index.htm",
            settings=_settings(),
            org_unit_id=org_id,
            org_unit_name="数学学院",
        )
    )
    html = """
    <html><body>
      <a href="/szdw/2.htm">2</a>
      <a href="/szdw/prof.htm">教授</a>
      <a href="javascript:document.forms['f'].PAGENUM.value='3';document.forms['f'].submit();">3</a>
      <a href="/teacher/info/1.htm">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/szdw/index.htm", "https://x.edu.cn/szdw/index.htm", "")

    node = ClaimedNode(
        id=parent_id,
        node_key="parent",
        type=NodeType.faculty_list_url,
        url=snap.url,
        org_unit_id=org_id,
        org_unit_name="数学学院",
        depth=0,
        attempt_count=1,
        priority_score=80,
        content_hash=None,
        metadata=None,
    )
    deps = HandlerDeps(
        storage=h,
        llm_client=None,
        settings=_settings(),
        run_id=1,
        university_name="测试大学",
        extract_queue=asyncio.Queue(),
        raw_html=html,
    )
    await _create_url_pagination_nodes(node, snap, deps)
    await _create_followup_nodes(node, snap, deps)
    await _create_form_pagination_nodes(node, snap, deps)

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        edges = (await s.execute(select(GraphEdge))).scalars().all()
        assert sum(1 for n in nodes if n.type == NodeType.pagination_url) == 2
        assert sum(1 for n in nodes if n.type == NodeType.faculty_followup_url) == 1
        assert {e.edge_type for e in edges} == {EdgeType.pagination_of, EdgeType.discovered_on_page}
        form_node = next(n for n in nodes if (n.metadata_json or {}).get("pagination_kind") == "form")
        action = fetch_action_from_metadata(form_node.metadata_json)
        assert action.synthetic_url == form_node.metadata_json["identity_url"]
        assert action.page_index == 3
    await _close(h)


async def test_cross_org_shared_detail_url_creates_two_nodes(tmp_path):
    h = await _storage(tmp_path)
    settings = _settings()
    org1 = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    org2 = await h.writer.upsert_org_unit(OrgUnitSpec(name="交叉学院", url="https://x.edu.cn/cross"))
    await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/t/1", settings=settings, org_unit_id=org1, org_unit_name="数学学院")
    )
    await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/t/1", settings=settings, org_unit_id=org2, org_unit_name="交叉学院")
    )
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalars().all()
        assert len(nodes) == 2
        assert {n.org_unit_id for n in nodes} == {org1, org2}
        assert len({n.node_key for n in nodes}) == 2
    await _close(h)


def test_detail_like_teacher_url_is_excluded_from_root_pagination_candidates():
    html = """
    <html><body>
      <a href="/teacher/info/1001.htm">张三 教授</a>
      <a href="/szdw/2.htm">2</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/szdw.htm", "https://x.edu.cn/szdw.htm", "")
    excluded = _detail_like_urls(snap)
    assert "https://x.edu.cn/teacher/info/1001.htm" in excluded
    assert "https://x.edu.cn/szdw/2.htm" not in excluded
