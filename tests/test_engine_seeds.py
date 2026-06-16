import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.engine import PRIORITY_BY_TYPE, load_seed_nodes
from dext.engine.seeds import node_spec
from dext.seed import OrgUnitSeed, UniversitySeed
from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import EdgeType, GraphEdge, GraphNode, NodeStatus, NodeType, OrgUnit
from dext.storage.writer import DBWriter


async def _writer(tmp_path):
    eng = create_engine_for_path(tmp_path / "engine-seeds.db")
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
    return SimpleNamespace(max_attempts=3)


def test_node_spec_uses_constant_priority():
    spec = node_spec(NodeType.detail_url, url="https://x/t", settings=_settings(), org_unit_id=7)
    assert spec.base_priority == PRIORITY_BY_TYPE[NodeType.detail_url]
    assert spec.priority_score == spec.base_priority
    assert spec.max_attempts == 3


async def test_load_seed_nodes_is_idempotent_and_bootstraps_direct_faculty_urls(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["/schools.htm"],
        org_units=[
            OrgUnitSeed(name="数学学院", url="/math", faculty_urls=["/math/teachers.htm"]),
            OrgUnitSeed(name="物理学院", faculty_urls=["https://x.edu.cn/physics/teachers.htm"]),
        ],
    )

    first = await load_seed_nodes(university, h, _settings(), run_id=1)
    second = await load_seed_nodes(university, h, _settings(), run_id=1)

    assert first.org_listing_nodes == second.org_listing_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        orgs = (await s.execute(select(OrgUnit))).scalars().all()
        edges = (await s.execute(select(GraphEdge))).scalars().all()
        assert len(orgs) == 2
        assert len(nodes) == 5
        assert len(edges) == 2
        synthetic = next(n for n in nodes if n.org_unit_name == "物理学院" and n.type == NodeType.org_unit)
        assert synthetic.status == NodeStatus.skipped
        assert synthetic.url == "about:org_unit:物理学院"
        assert {e.edge_type for e in edges} == {EdgeType.belongs_to_org_unit}
    await _close(h)


async def test_load_seed_nodes_skips_explicit_port_urls(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn:443",
        org_unit_listing_urls=["/schools.htm", "https://ok.edu.cn/schools.htm"],
        org_units=[
            OrgUnitSeed(name="端口学院", url="https://x.edu.cn:8080/math", faculty_urls=["/math/teachers.htm"]),
            OrgUnitSeed(name="正常学院", url="https://ok.edu.cn/math", faculty_urls=[
                "https://ok.edu.cn:443/math/teachers.htm",
                "https://ok.edu.cn/math/teachers.htm",
            ]),
        ],
    )

    summary = await load_seed_nodes(university, h, _settings(), run_id=1)

    assert summary.org_listing_nodes == 1
    assert summary.org_units == 1
    assert summary.faculty_list_nodes == 1
    async with h.session_factory() as s:
        urls = {n.url for n in (await s.execute(select(GraphNode))).scalars().all()}
        assert "https://ok.edu.cn/schools.htm" in urls
        assert "https://ok.edu.cn/math" in urls
        assert "https://ok.edu.cn/math/teachers.htm" in urls
        assert all(":443" not in url and ":8080" not in url for url in urls)
    await _close(h)
