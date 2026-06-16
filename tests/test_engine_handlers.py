import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.engine.handlers import (
    HandlerDeps,
    handle_faculty_page,
    _create_form_pagination_nodes,
    _create_child,
    _create_url_pagination_nodes,
    _detail_like_urls,
    handle_org_listing,
    fetch_action_from_metadata,
)
from dext.engine.seeds import node_spec
from dext.page.links import build_snapshot
from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import EdgeType, GraphEdge, GraphNode, NodeStatus, NodeType, OrgUnit
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


async def test_faculty_page_creates_url_and_form_pagination_nodes(tmp_path):
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
    await _create_form_pagination_nodes(node, snap, deps)

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        edges = (await s.execute(select(GraphEdge))).scalars().all()
        assert sum(1 for n in nodes if n.type == NodeType.pagination_url) == 2
        assert sum(1 for n in nodes if n.type == NodeType.faculty_followup_url) == 0
        assert {e.edge_type for e in edges} == {EdgeType.pagination_of}
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


async def test_org_listing_uses_normalized_college_name(tmp_path):
    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm", settings=_settings(), run_id=1)
    )
    html = """
    <html><body>
      <a href="/math.htm">数学学院（信息与计算科学系</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(
                    url="https://x.edu.cn/math.htm",
                    label="college",
                    confidence=0.9,
                    is_leaf=False,
                    org_unit_name="数学学院（信息与计算科学系",
                )
            ]
        )

    import dext.engine.handlers as handlers_mod

    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(
            id=listing_id,
            node_key="listing",
            type=NodeType.org_listing_url,
            url=snap.url,
            org_unit_id=None,
            org_unit_name=None,
            depth=0,
            attempt_count=1,
            priority_score=100,
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
        await handle_org_listing(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        org = (await s.execute(select(OrgUnit))).scalar_one()
        assert org.name == "数学学院（信息与计算科学系）"
        n = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.org_unit))).scalar_one()
        assert n.status == NodeStatus.pending
        assert n.priority_score == 90
    await _close(h)


async def test_org_listing_only_creates_non_excluded_colleges(tmp_path):
    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm", settings=_settings(), run_id=1)
    )
    html = "<html><body><a href='/a'>艺术学院</a><a href='/b'>国际关系学院</a></body></html>"
    snap = build_snapshot(html, "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[
            SimpleNamespace(url="https://x.edu.cn/a", label="noise", confidence=0.9,
                            is_leaf=False, org_unit_name="艺术学院", exclusion_reason="arts"),
            SimpleNamespace(url="https://x.edu.cn/b", label="college", confidence=0.9,
                            is_leaf=False, org_unit_name="国际关系学院", exclusion_reason=None),
        ])

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=listing_id, node_key="listing", type=NodeType.org_listing_url,
                           url=snap.url, org_unit_id=None, org_unit_name=None, depth=0,
                           attempt_count=1, priority_score=100, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_org_listing(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        orgs = (await s.execute(select(OrgUnit))).scalars().all()
        assert [o.name for o in orgs] == ["国际关系学院"]
    await _close(h)


async def test_faculty_page_skipped_when_page_exclusion_reason_set(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="经济学院", url="https://x.edu.cn/econ/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/econ/people.htm",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="经济学院")
    )
    # 标题不含任何确定性标记词：旧的启发式不会跳过（会标 done），只有 decider 的
    # page_exclusion_reason 能让新代码跳过 —— 形成干净的 RED。
    snap = build_snapshot("<html><head><title>经济学院 教师队伍</title></head><body>教师名录</body></html>",
                          "https://x.edu.cn/econ/people.htm", "https://x.edu.cn/econ/people.htm", "经济学院 教师队伍")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], page_is_leaf=False,
                               page_exclusion_reason="postdoc", parse_error=None)

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="经济学院", depth=1, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html="")
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        row = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert row.status == NodeStatus.skipped
        assert row.last_error == "excluded:postdoc"
    await _close(h)


async def test_faculty_page_llm_followup_decision_creates_category_nodes(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="法学院（律师学院）", url="https://law.scu.edu.cn/"))
    node_id = await h.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
            settings=_settings(),
            run_id=1,
            org_unit_id=org_id,
            org_unit_name="法学院（律师学院）",
        )
    )
    html = """
    <html><body>
      <a href="/szdw/zzjzg_link/fgzc.htm">副高职称</a>
      <a href="/szdw/zzjzg_link/zjzc.htm">中级职称</a>
      <a href="/info/1360/15754.htm">左卫民</a>
    </body></html>
    """
    snap = build_snapshot(
        html,
        "https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        "https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        "正高职称-四川大学法学院",
    )

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(
                    url="https://law.scu.edu.cn/szdw/zzjzg_link/fgzc.htm",
                    label="followup",
                    confidence=0.95,
                    is_leaf=False,
                ),
                SimpleNamespace(
                    url="https://law.scu.edu.cn/szdw/zzjzg_link/zjzc.htm",
                    label="followup",
                    confidence=0.95,
                    is_leaf=False,
                ),
                SimpleNamespace(
                    url="https://law.scu.edu.cn/info/1360/15754.htm",
                    label="detail",
                    confidence=0.9,
                    is_leaf=True,
                ),
            ],
            parse_error=None,
            page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod

    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(
            id=node_id,
            node_key="faculty",
            type=NodeType.faculty_list_url,
            url=snap.url,
            org_unit_id=org_id,
            org_unit_name="法学院（律师学院）",
            depth=1,
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
            university_name="四川大学",
            extract_queue=asyncio.Queue(),
            raw_html=html,
        )
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        details = [n for n in nodes if n.type == NodeType.detail_url]
        parent = next(n for n in nodes if n.id == node_id)
        assert parent.status == NodeStatus.done
        assert {n.url for n in followups} == {
            "https://law.scu.edu.cn/szdw/zzjzg_link/fgzc.htm",
            "https://law.scu.edu.cn/szdw/zzjzg_link/zjzc.htm",
        }
        assert [n.url for n in details] == ["https://law.scu.edu.cn/info/1360/15754.htm"]
    await _close(h)


async def test_faculty_page_retries_when_llm_selects_no_navigation(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="法学院（律师学院）", url="https://law.scu.edu.cn/"))
    node_id = await h.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
            settings=_settings(),
            run_id=1,
            org_unit_id=org_id,
            org_unit_name="法学院（律师学院）",
        )
    )
    html = """
    <html><body>
      <a href="/szdw/zzjzg_link/fgzc.htm">副高职称</a>
      <a href="/szdw/zzjzg_link/zjzc.htm">中级职称</a>
    </body></html>
    """
    snap = build_snapshot(
        html,
        "https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        "https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        "正高职称-四川大学法学院",
    )

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], parse_error=None, page_exclusion_reason=None)

    import dext.engine.handlers as handlers_mod

    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(
            id=node_id,
            node_key="faculty",
            type=NodeType.faculty_list_url,
            url=snap.url,
            org_unit_id=org_id,
            org_unit_name="法学院（律师学院）",
            depth=1,
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
            university_name="四川大学",
            extract_queue=asyncio.Queue(),
            raw_html=html,
        )
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        row = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert row.status == NodeStatus.retry
        assert row.last_error == "decider_no_navigation_links"
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
