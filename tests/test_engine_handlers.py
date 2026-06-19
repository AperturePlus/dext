import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.bridge.probe import StatusProbe
from dext.bridge.redirect import RedirectGuard
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


def _status_probe(status_by_url: dict[str, int] | None = None, *, raise_urls=()):
    """Fake StatusProbe resolver: returns (url, status) for given URLs, raises
    for raise_urls (simulates probe-side unreachable / redirect-loop)."""

    async def resolver(url):
        if url in raise_urls:
            raise RuntimeError("probe unreachable")
        return url, status_by_url.get(url, 200)

    return StatusProbe(resolver=resolver)


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


async def test_cross_org_shared_detail_url_keeps_one_node_and_adds_edges(tmp_path):
    h = await _storage(tmp_path)
    settings = _settings()
    org1 = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    org2 = await h.writer.upsert_org_unit(OrgUnitSpec(name="交叉学院", url="https://x.edu.cn/cross"))
    parent1 = await h.writer.upsert_node(
        node_spec(NodeType.org_unit, url="https://x.edu.cn/math", settings=settings, org_unit_id=org1, org_unit_name="数学学院")
    )
    parent2 = await h.writer.upsert_node(
        node_spec(NodeType.org_unit, url="https://x.edu.cn/cross", settings=settings, org_unit_id=org2, org_unit_name="交叉学院")
    )
    parent_node1 = ClaimedNode(
        id=parent1,
        node_key="p1",
        type=NodeType.org_unit,
        url="https://x.edu.cn/math",
        org_unit_id=org1,
        org_unit_name="数学学院",
        depth=0,
        attempt_count=1,
        priority_score=1,
        content_hash=None,
        metadata=None,
    )
    parent_node2 = ClaimedNode(
        id=parent2,
        node_key="p2",
        type=NodeType.org_unit,
        url="https://x.edu.cn/cross",
        org_unit_id=org2,
        org_unit_name="交叉学院",
        depth=0,
        attempt_count=1,
        priority_score=1,
        content_hash=None,
        metadata=None,
    )
    deps = HandlerDeps(storage=h, llm_client=None, settings=settings, run_id=1, university_name="测试大学",
                       extract_queue=asyncio.Queue(), raw_html="", redirect_guard=None)
    await _create_child(
        deps,
        parent_node1,
        NodeType.detail_url,
        url="https://x.edu.cn/t/1",
        edge_type=EdgeType.detail_candidate_of,
        metadata={"label": "detail"},
    )
    await _create_child(
        deps,
        parent_node2,
        NodeType.detail_url,
        url="https://x.edu.cn/t/1",
        edge_type=EdgeType.detail_candidate_of,
        metadata={"label": "detail"},
    )
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalars().all()
        assert len(nodes) == 1
        assert nodes[0].org_unit_id == org1
        assert nodes[0].url == "https://x.edu.cn/t/1"
        edges = (await s.execute(select(GraphEdge))).scalars().all()
        assert len(edges) == 2
    await _close(h)


async def test_create_child_uses_redirect_final_url_and_blocks_bad_redirects(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    parent_id = await h.writer.upsert_node(
        node_spec(NodeType.org_unit, url="https://x.edu.cn/math", settings=_settings(), run_id=1,
                  org_unit_id=org_id, org_unit_name="数学学院")
    )

    async def resolver(url):
        if url == "https://x.edu.cn/t/1":
            return "https://teacher.x.edu.cn/t/1"
        return "https://mp.weixin.qq.com/s/bad"

    from dext.bridge.redirect import RedirectGuard

    deps = HandlerDeps(
        storage=h,
        llm_client=None,
        settings=_settings(),
        run_id=1,
        university_name="测试大学",
        extract_queue=asyncio.Queue(),
        raw_html="",
        redirect_guard=RedirectGuard(resolver=resolver),
    )
    parent = ClaimedNode(
        id=parent_id,
        node_key="p",
        type=NodeType.org_unit,
        url="https://x.edu.cn/math",
        org_unit_id=org_id,
        org_unit_name="数学学院",
        depth=0,
        attempt_count=1,
        priority_score=1,
        content_hash=None,
        metadata=None,
    )
    child_id = await _create_child(
        deps,
        parent,
        NodeType.detail_url,
        url="https://x.edu.cn/t/1",
        edge_type=EdgeType.detail_candidate_of,
        metadata={"label": "detail"},
    )
    blocked_id = await _create_child(
        deps,
        parent,
        NodeType.detail_url,
        url="https://x.edu.cn/block",
        edge_type=EdgeType.detail_candidate_of,
        metadata={"label": "detail"},
    )

    async with h.session_factory() as s:
        child = (await s.execute(select(GraphNode).where(GraphNode.id == child_id))).scalar_one()
        assert child.url == "https://teacher.x.edu.cn/t/1"
        assert (child.metadata_json or {})["discovered_from_url"] == "https://x.edu.cn/t/1"
        assert blocked_id is None
        assert len((await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalars().all()) == 1
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


async def test_faculty_page_llm_exclusion_reason_suppresses_child_nodes_even_if_leaf(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="法学院", url="https://x.edu.cn/law/"))
    node_id = await h.writer.upsert_node(
        node_spec(
            NodeType.faculty_list_url,
            url="https://x.edu.cn/law/people.htm",
            settings=_settings(),
            run_id=1,
            org_unit_id=org_id,
            org_unit_name="法学院",
        )
    )
    html = """
    <html><body>
      <a href="/law/postdoc.htm">博士后</a>
      <a href="/law/industry.htm">行业导师</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/law/people.htm", "https://x.edu.cn/law/people.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(
                    url="https://x.edu.cn/law/postdoc.htm",
                    label="detail",
                    confidence=0.95,
                    is_leaf=True,
                    exclusion_reason="postdoc",
                ),
                SimpleNamespace(
                    url="https://x.edu.cn/law/industry.htm",
                    label="followup",
                    confidence=0.95,
                    is_leaf=False,
                    exclusion_reason="industry_mentor",
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
            org_unit_name="法学院",
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
            university_name="测试大学",
            extract_queue=asyncio.Queue(),
            raw_html=html,
        )
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        parent = next(n for n in nodes if n.id == node_id)
        assert parent.status == NodeStatus.done
        assert [n for n in nodes if n.id != node_id] == []
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


async def test_faculty_page_drops_reslice_when_people_present(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="哲学系", url="https://x.edu.cn/phil/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/phil/index.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="哲学系")
    )
    html = """
    <html><body>
      <a href="/phil/jiaoshou/index.html">教授</a>
      <a href="/phil/fujiaoshou/index.html">副教授</a>
      <a href="/phil/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/phil/index.html", "https://x.edu.cn/phil/index.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/phil/jiaoshou/index.html", label="reslice",
                                confidence=0.9, is_leaf=False, facet_axis="title"),
                SimpleNamespace(url="https://x.edu.cn/phil/fujiaoshou/index.html", label="reslice",
                                confidence=0.9, is_leaf=False, facet_axis="title"),
                SimpleNamespace(url="https://x.edu.cn/phil/teacher/1.html", label="detail",
                                confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="哲学系", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        details = [n for n in nodes if n.type == NodeType.detail_url]
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        parent = next(n for n in nodes if n.id == node_id)
        assert [n.url for n in details] == ["https://x.edu.cn/phil/teacher/1.html"]
        assert followups == []  # both title re-slices collapsed
        assert parent.status == NodeStatus.done
    await _close(h)


async def test_faculty_page_takes_one_axis_when_no_people(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="某系", url="https://x.edu.cn/u/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/u/people.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="某系")
    )
    # No detail/pagination/followup links; only two reslice axes (letter + title).
    html = """
    <html><body>
      <a href="/u/letter_a.html">A</a>
      <a href="/u/letter_b.html">B</a>
      <a href="/u/prof.html">教授</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/u/people.html", "https://x.edu.cn/u/people.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/u/letter_a.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="letter"),
                SimpleNamespace(url="https://x.edu.cn/u/letter_b.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="letter"),
                SimpleNamespace(url="https://x.edu.cn/u/prof.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="title"),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="某系", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        assert {n.url for n in followups} == {
            "https://x.edu.cn/u/letter_a.html", "https://x.edu.cn/u/letter_b.html",
        }  # letter axis chosen (priority over title); title dropped
        assert all((n.metadata_json or {}).get("facet_axis") == "letter" for n in followups)
    await _close(h)


async def test_faculty_page_drops_query_filter_buttons_when_wide_page_has_people(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="管理学院", url="https://x.edu.cn/szdw/zrjs"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/szdw/zrjs",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="管理学院")
    )
    html = """
    <html><body>
      <a href="/szdw/zrjs?keyword=&yjjg=&jxx=&jobType=教授">教授</a>
      <a href="/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=">公共管理系</a>
      <a href="/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=&jobType=">智能财务管理研究所</a>
      <a href="/szdw/zrjs/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/szdw/zrjs", "https://x.edu.cn/szdw/zrjs", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=&jobType=教授",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs/teacher/1.html",
                                label="detail", confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="管理学院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        details = [n for n in nodes if n.type == NodeType.detail_url]
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        assert [n.url for n in details] == ["https://x.edu.cn/szdw/zrjs/teacher/1.html"]
        assert followups == []
    await _close(h)


async def test_faculty_page_creates_one_query_filter_reset_to_wider_table(tmp_path):
    h = await _storage(tmp_path)
    current_url = (
        "https://x.edu.cn/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&"
        "jxx=公共管理系&jobType=教授"
    )
    all_url = "https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=&jobType="
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="管理学院", url=current_url))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_followup_url, url=current_url,
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="管理学院")
    )
    html = f"""
    <html><body>
      <a href="{all_url}">全部</a>
      <a href="/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=公共管理系&jobType=副教授">副教授</a>
      <a href="/szdw/zrjs?keyword=&yjjg=数字营销管理研究所&jxx=公共管理系&jobType=教授">数字营销管理研究所</a>
      <a href="/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=组织管理系&jobType=教授">组织管理系</a>
      <a href="/szdw/zrjs/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, current_url, current_url, "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url=all_url, label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=公共管理系&jobType=副教授",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=数字营销管理研究所&jxx=公共管理系&jobType=教授",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=组织管理系&jobType=教授",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs/teacher/1.html",
                                label="detail", confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_followup_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="管理学院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url and n.id != node_id]
        assert [n.url for n in followups] == [all_url]
        metadata = followups[0].metadata_json or {}
        assert metadata["facet_kind"] == "query_filter"
        assert metadata["facet_axis"] == "query:jxx"
        assert metadata["facet_value"] == ""
        assert metadata["facet_is_all"] is True
    await _close(h)


async def test_faculty_page_takes_one_query_filter_axis_when_no_people_or_reset(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="管理学院", url="https://x.edu.cn/szdw/zrjs"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/szdw/zrjs",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="管理学院")
    )
    html = """
    <html><body>
      <a href="/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=">公共管理系</a>
      <a href="/szdw/zrjs?keyword=&yjjg=&jxx=组织管理系&jobType=">组织管理系</a>
      <a href="/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=&jobType=">智能财务管理研究所</a>
      <a href="/szdw/zrjs?keyword=&yjjg=数字营销管理研究所&jxx=&jobType=">数字营销管理研究所</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/szdw/zrjs", "https://x.edu.cn/szdw/zrjs", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=组织管理系&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=智能财务管理研究所&jxx=&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=数字营销管理研究所&jxx=&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="管理学院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        assert {n.url for n in followups} == {
            "https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=",
            "https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=组织管理系&jobType=",
        }
        assert all((n.metadata_json or {}).get("facet_axis") == "query:jxx" for n in followups)
    await _close(h)


async def test_faculty_page_keeps_path_followup_while_dropping_query_filters(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="管理学院", url="https://x.edu.cn/szdw/zrjs"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/szdw/zrjs",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="管理学院")
    )
    html = """
    <html><body>
      <a href="/szdw/cyjxjs">产业教学教师</a>
      <a href="/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=">公共管理系</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/szdw/zrjs", "https://x.edu.cn/szdw/zrjs", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/szdw/cyjxjs",
                                label="followup", confidence=0.8, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/szdw/zrjs?keyword=&yjjg=&jxx=公共管理系&jobType=",
                                label="followup", confidence=0.8, is_leaf=False),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="管理学院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        assert [n.url for n in followups] == ["https://x.edu.cn/szdw/cyjxjs"]
    await _close(h)


from dext.bridge.decision import DecisionCenter


def _settings_budget(n):
    return SimpleNamespace(max_attempts=3, max_depth=4, followup_page_limit=36, facet_node_budget=n)


async def test_faculty_page_facet_budget_stops_expansion_and_sets_decision(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="爆炸院", url="https://x.edu.cn/boom/"))
    # Pre-seed 2 followup nodes; together with this faculty_list node that is 3 facet
    # nodes for the subtree, which is >= the budget of 2 → over budget.
    for i in range(2):
        await h.writer.upsert_node(
            node_spec(NodeType.faculty_followup_url, url=f"https://x.edu.cn/boom/seed{i}.html",
                      settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="爆炸院")
        )
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/boom/index.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="爆炸院")
    )
    html = """
    <html><body>
      <a href="/boom/more.html">系所</a>
      <a href="/boom/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/boom/index.html", "https://x.edu.cn/boom/index.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/boom/more.html", label="followup",
                                confidence=0.9, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/boom/teacher/1.html", label="detail",
                                confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    center = DecisionCenter()
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="爆炸院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings_budget(2), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html,
                           decision_center=center)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        # detail leaf still created (not budget-limited); no NEW followup beyond the 2 seeds.
        assert any(n.type == NodeType.detail_url and n.url.endswith("/teacher/1.html") for n in nodes)
        assert sum(1 for n in nodes if n.type == NodeType.faculty_followup_url) == 2
    assert center.current() is not None
    assert center.current().kind == "facet_budget"
    await _close(h)


# --- 入图前 status probe 覆盖新发现的子节点 URL（防止 502/太多重定向阻塞前端）---


async def _create_child_with_probe(tmp_path, *, status_probe, url="https://x.edu.cn/t/1"):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    parent_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/math/list.htm", settings=_settings(),
                  run_id=1, org_unit_id=org_id, org_unit_name="数学学院")
    )
    # parent is the already-crawled faculty page — mark done so claim_next below
    # only reflects whether the *child* is claimable (the probe-under-test outcome).
    await h.writer.mark_node(parent_id, NodeStatus.done)
    deps = HandlerDeps(
        storage=h,
        llm_client=None,
        settings=_settings(),
        run_id=1,
        university_name="测试大学",
        extract_queue=asyncio.Queue(),
        raw_html="",
        redirect_guard=RedirectGuard(),  # default resolver unused: status probe runs first on same host
        status_probe=status_probe,
    )
    parent = ClaimedNode(
        id=parent_id, node_key="p", type=NodeType.faculty_list_url, url="https://x.edu.cn/math/list.htm",
        org_unit_id=org_id, org_unit_name="数学学院", depth=1, attempt_count=1,
        priority_score=80, content_hash=None, metadata=None,
    )
    child_id = await _create_child(
        deps, parent, NodeType.detail_url, url=url,
        edge_type=EdgeType.detail_candidate_of, metadata={"label": "detail"},
    )
    return h, child_id


async def test_create_child_502_defers_node_not_claimable(tmp_path):
    """5xx 子节点入图后标 retry+next_retry_at,本 run claim_next 跳过 → 不交给前端 fetch."""
    h, child_id = await _create_child_with_probe(
        tmp_path, status_probe=_status_probe({"https://x.edu.cn/t/1": 502})
    )
    assert child_id is not None
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == child_id))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.next_retry_at is not None
        assert node.last_error == "http_502"
    assert await h.writer.claim_next(run_id=1, exclude_node_keys=set()) is None
    await _close(h)


async def test_create_child_probe_failed_defers_node(tmp_path):
    """探针侧不可达(redirect-loop/超时)→ 同样延迟,避免浏览器卡死在坏宿主."""
    h, child_id = await _create_child_with_probe(
        tmp_path, status_probe=_status_probe(raise_urls=("https://x.edu.cn/t/1",))
    )
    assert child_id is not None
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == child_id))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.next_retry_at is not None
        assert node.last_error == "probe_failed"
    assert await h.writer.claim_next(run_id=1, exclude_node_keys=set()) is None
    await _close(h)


async def test_create_child_dead_404_marks_skipped(tmp_path):
    """404/410 死链子节点建 skipped 节点(保留图完整性 + 可诊断),claim_next 不取."""
    h, child_id = await _create_child_with_probe(
        tmp_path, status_probe=_status_probe({"https://x.edu.cn/t/1": 404})
    )
    assert child_id is not None
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == child_id))).scalar_one()
        assert node.status == NodeStatus.skipped
        assert (node.metadata_json or {}).get("reason") == "http_404"
        assert (node.metadata_json or {}).get("probe_skipped") is True
    # skipped 节点不被 claim_next 取(只取 pending/retry)
    assert await h.writer.claim_next(run_id=1, exclude_node_keys=set()) is None
    await _close(h)


async def test_create_child_ok_keeps_pending_claimable(tmp_path):
    """200 活链接子节点正常 pending,无 defer/skip 标记,claim_next 可取 —— 不误伤."""
    h, child_id = await _create_child_with_probe(
        tmp_path, status_probe=_status_probe({"https://x.edu.cn/t/1": 200})
    )
    assert child_id is not None
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == child_id))).scalar_one()
        assert node.status == NodeStatus.pending
        assert node.next_retry_at is None
        assert "probe_defer_reason" not in (node.metadata_json or {})
        assert "probe_skip_reason" not in (node.metadata_json or {})
    claimed = await h.writer.claim_next(run_id=1, exclude_node_keys=set())
    assert claimed is not None and claimed.url == "https://x.edu.cn/t/1"
    await _close(h)


async def test_org_listing_502_college_child_is_deferred(tmp_path):
    """handle_org_listing 路径:502 college 链接的子节点 defer,本 run 不被 claim."""
    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm", settings=_settings(), run_id=1)
    )
    html = '<html><body><a href="/math.htm">数学学院</a></body></html>'
    snap = build_snapshot(html, "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[
            SimpleNamespace(url="https://x.edu.cn/math.htm", label="college", confidence=0.9,
                            is_leaf=False, org_unit_name="数学学院", exclusion_reason=None),
        ], parse_error=None)

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=listing_id, node_key="listing", type=NodeType.org_listing_url,
                           url=snap.url, org_unit_id=None, org_unit_name=None, depth=0,
                           attempt_count=1, priority_score=100, content_hash=None, metadata=None)
        deps = HandlerDeps(
            storage=h, llm_client=None, settings=_settings(), run_id=1, university_name="测试大学",
            extract_queue=asyncio.Queue(), raw_html=html,
            redirect_guard=RedirectGuard(),
            status_probe=_status_probe({"https://x.edu.cn/math.htm": 502}),
        )
        await handle_org_listing(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        org_node = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.org_unit))).scalar_one()
        assert org_node.status == NodeStatus.retry
        assert org_node.next_retry_at is not None
        assert org_node.last_error == "http_502"
    assert await h.writer.claim_next(run_id=1, exclude_node_keys=set()) is None
    await _close(h)


async def test_faculty_page_502_detail_child_is_deferred(tmp_path):
    """handle_faculty_page → _materialize_decided 路径:502 detail 链接的子节点 defer,
    本 run 不被 claim → 不交给前端 fetch(用户要求的核心断言)."""
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/math/list.htm",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="数学学院")
    )
    html = '<html><body><a href="/math/t/zhang.htm">张三</a></body></html>'
    snap = build_snapshot(html, "https://x.edu.cn/math/list.htm", "https://x.edu.cn/math/list.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[
            SimpleNamespace(url="https://x.edu.cn/math/t/zhang.htm", label="detail",
                            confidence=0.9, is_leaf=True, exclusion_reason=None),
        ], parse_error=None, page_exclusion_reason=None)

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="数学学院", depth=1, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(
            storage=h, llm_client=None, settings=_settings(), run_id=1, university_name="测试大学",
            extract_queue=asyncio.Queue(), raw_html=html,
            redirect_guard=RedirectGuard(),
            status_probe=_status_probe({"https://x.edu.cn/math/t/zhang.htm": 502}),
        )
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        detail = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalar_one()
        assert detail.status == NodeStatus.retry
        assert detail.next_retry_at is not None
        assert detail.last_error == "http_502"
        parent = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert parent.status == NodeStatus.done
    # detail 节点本 run 不被 claim → 浏览器不会被 502 URL 阻塞
    claimed = await h.writer.claim_next(run_id=1, exclude_node_keys=set())
    assert claimed is None or claimed.url != "https://x.edu.cn/math/t/zhang.htm"
    await _close(h)
