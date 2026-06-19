import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from dext.engine import PRIORITY_BY_TYPE, load_seed_nodes
from dext.engine.seeds import node_spec, resolve_discovered_url, resolve_discovered_urls
from dext.bridge.redirect import RedirectGuard
from dext.bridge.probe import StatusProbe
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


async def test_load_seed_nodes_drops_blocked_redirect_urls(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["/schools.htm"],
        org_units=[OrgUnitSeed(name="数学学院", url="/math", faculty_urls=["/math/teachers.htm"])],
    )

    async def resolver(url):
        return "https://mp.weixin.qq.com/s/abc"

    guard = RedirectGuard(resolver=resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, redirect_guard=guard)

    assert summary.org_listing_nodes == 0
    assert summary.org_units == 0
    assert summary.faculty_list_nodes == 0
    async with h.session_factory() as s:
        assert (await s.execute(select(GraphNode))).scalars().all() == []
    await _close(h)


async def test_resolve_discovered_url_blocks_disallowed_direct():
    resolved, metadata = await resolve_discovered_url("https://evil.com/p")
    assert resolved is None
    assert metadata == {}


async def test_resolve_discovered_url_blocks_disallowed_redirect():
    async def resolver(url):
        return "https://evil.com/"

    guard = RedirectGuard(resolver=resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", redirect_guard=guard)
    assert resolved is None
    assert metadata == {}


async def test_resolve_discovered_url_allows_github_io_redirect():
    async def resolver(url):
        return "https://foo.github.io/page"

    guard = RedirectGuard(resolver=resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", redirect_guard=guard)
    assert resolved == "https://foo.github.io/page"
    assert metadata["source_url"] == "https://x.edu.cn/p"


async def test_resolve_discovered_url_probe_failed_is_not_dropped():
    async def resolver(url):
        raise RuntimeError("WAF")

    guard = RedirectGuard(resolver=resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", redirect_guard=guard)
    assert resolved == "https://x.edu.cn/p"
    assert metadata["source_url"] == "https://x.edu.cn/p"
    assert metadata["redirect_probe_failed"] is True


async def test_resolve_discovered_url_skips_probe_for_non_http_urls():
    async def resolver(url):
        raise AssertionError("non-http URL must not be probed")

    guard = RedirectGuard(resolver=resolver)
    resolved, metadata = await resolve_discovered_url("about:org_unit:数学学院", redirect_guard=guard)
    assert resolved == "about:org_unit:数学学院"
    assert metadata["source_url"] == "about:org_unit:数学学院"


async def test_resolve_discovered_urls_runs_probes_concurrently():
    import asyncio as _asyncio

    in_flight = 0
    peak = 0
    lock = _asyncio.Lock()

    async def resolver(url):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await _asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return url

    guard = RedirectGuard(resolver=resolver)
    urls = [f"https://x.edu.cn/p{i}" for i in range(32)]
    results = await resolve_discovered_urls(urls, redirect_guard=guard)
    assert len(results) == 32
    assert all(resolved_url == url for (resolved_url, _meta), url in zip(results, urls))
    # Serial execution would peak at 1; semaphore(16) allows real concurrency.
    assert peak > 1


async def test_load_seed_nodes_keeps_probe_failed_urls(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["/schools.htm"],
        org_units=[OrgUnitSeed(name="数学学院", url="/math", faculty_urls=["/math/teachers.htm"])],
    )

    class TooManyRedirects(Exception):
        pass

    async def resolver(url):
        raise TooManyRedirects("too many redirects")

    guard = RedirectGuard(resolver=resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, redirect_guard=guard)

    # PROBE_FAILED no longer drops URLs — the seed nodes are still created.
    assert summary.org_listing_nodes == 1
    assert summary.org_units == 1
    assert summary.faculty_list_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        listing = next(n for n in nodes if n.type == NodeType.org_listing_url)
        assert listing.metadata_json.get("redirect_probe_failed") is True
    await _close(h)


def _status_probe(resolver):
    return StatusProbe(resolver=resolver)


async def test_resolve_discovered_url_status_probe_dead_returns_skip_marker():
    async def resolver(url):
        return url, 404

    probe = _status_probe(resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", status_probe=probe)
    assert resolved is None
    assert metadata.get("probe_skip_reason") == "http_404"


async def test_resolve_discovered_url_status_probe_rate_limited_keeps_url():
    async def resolver(url):
        return url, 429

    probe = _status_probe(resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", status_probe=probe)
    assert resolved == "https://x.edu.cn/p"
    assert metadata["probe_status"] == "http_429"


async def test_resolve_discovered_url_status_probe_transient_defers():
    async def resolver(url):
        return url, 503

    probe = _status_probe(resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", status_probe=probe)
    assert resolved == "https://x.edu.cn/p"
    assert metadata["probe_defer_reason"] == "http_503"


async def test_resolve_discovered_url_status_probe_failed_defers():
    async def resolver(url):
        raise RuntimeError("WAF")

    probe = _status_probe(resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", status_probe=probe)
    assert resolved == "https://x.edu.cn/p"
    assert metadata["probe_defer_reason"] == "probe_failed"


async def test_resolve_discovered_url_status_probe_timeout_keeps_pending():
    # A probe timeout is "no signal", not a block: the human browser often loads a
    # slow-to-probe host fine. The URL must stay pending (no defer, no dead-flag,
    # no drop) so it reaches the fetcher this run — not be shelved for 24h.
    async def resolver(url):
        raise TimeoutError("total timeout")

    probe = _status_probe(resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", status_probe=probe)
    assert resolved == "https://x.edu.cn/p"          # not dropped
    assert metadata["probe_timeout"] is True         # diagnostic marker
    assert "probe_defer_reason" not in metadata      # NOT deferred
    assert "probe_skip_reason" not in metadata       # NOT dead


async def test_resolve_discovered_url_redirect_probe_timeout_keeps_url():
    # Redirect-probe timeout: same "no signal" semantics — URL not dropped, not
    # deferred, just a more accurate reason marker than redirect_probe_failed.
    async def resolver(url):
        raise TimeoutError("total timeout")

    guard = RedirectGuard(resolver=resolver)
    resolved, metadata = await resolve_discovered_url("https://x.edu.cn/p", redirect_guard=guard)
    assert resolved == "https://x.edu.cn/p"               # not dropped
    assert metadata["redirect_probe_timeout"] is True     # diagnostic marker
    assert "redirect_probe_failed" not in metadata        # distinct from loop/DNS/WAF


async def test_load_seed_nodes_marks_dead_listing_as_skipped(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["https://x.edu.cn/schools.htm"],
        org_units=[],
    )

    async def resolver(url):
        return url, 404

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    assert summary.org_listing_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        assert len(nodes) == 1
        listing = nodes[0]
        assert listing.type == NodeType.org_listing_url.value
        assert listing.status == NodeStatus.skipped
        assert listing.metadata_json.get("reason") == "http_404"
        assert listing.metadata_json.get("probe_skipped") is True
    await _close(h)


async def test_load_seed_nodes_marks_dead_org_unit_as_skipped_but_keeps_faculty(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=[],
        org_units=[
            OrgUnitSeed(
                name="数学学院",
                url="https://x.edu.cn/math",
                faculty_urls=["https://x.edu.cn/math/teachers.htm"],
            ),
        ],
    )

    async def resolver(url):
        if url.endswith("/math"):
            return url, 410
        return url, 200

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    assert summary.org_units == 1
    assert summary.org_unit_nodes == 1
    assert summary.faculty_list_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        org_node = next(n for n in nodes if n.type == NodeType.org_unit.value)
        assert org_node.status == NodeStatus.skipped
        assert org_node.metadata_json.get("reason") == "http_410"
        faculty = next(n for n in nodes if n.type == NodeType.faculty_list_url.value)
        assert faculty.status == NodeStatus.pending
    await _close(h)


async def test_load_seed_nodes_marks_dead_faculty_as_skipped(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=[],
        org_units=[
            OrgUnitSeed(
                name="数学学院",
                url="https://x.edu.cn/math",
                faculty_urls=["https://x.edu.cn/math/teachers.htm"],
            ),
        ],
    )

    async def resolver(url):
        if url.endswith("teachers.htm"):
            return url, 404
        return url, 200

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    assert summary.faculty_list_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        faculty = next(n for n in nodes if n.type == NodeType.faculty_list_url.value)
        assert faculty.status == NodeStatus.skipped
        assert faculty.metadata_json.get("reason") == "http_404"
    await _close(h)


async def test_load_seed_nodes_rate_limited_keeps_pending(tmp_path):
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["https://x.edu.cn/schools.htm"],
        org_units=[],
    )

    async def resolver(url):
        return url, 429

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    assert summary.org_listing_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        assert nodes[0].status == NodeStatus.pending
        assert nodes[0].metadata_json.get("probe_status") == "http_429"
    await _close(h)


async def test_load_seed_nodes_transient_status_defers_listing(tmp_path):
    """5xx 入图前 → 节点标 retry + next_retry_at 延迟,本 run 不被 claim(不阻塞浏览器)."""
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["https://x.edu.cn/schools.htm"],
        org_units=[],
    )

    async def resolver(url):
        return url, 502

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    assert summary.org_listing_nodes == 1
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.next_retry_at is not None
        assert node.last_error == "http_502"
        assert node.metadata_json.get("probe_defer_reason") == "http_502"
        # claim_next must skip it (next_retry_at in the future → not claimed)
        claimed = await h.writer.claim_next(run_id=1, exclude_node_keys=set())
        assert claimed is None
    await _close(h)


async def test_load_seed_nodes_probe_failed_defers_faculty(tmp_path):
    """probe 侧不可达(redirect-loop/超时) → 同样延迟,避免浏览器卡死."""
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=[],
        org_units=[
            OrgUnitSeed(
                name="数学学院",
                url="https://x.edu.cn/math",
                faculty_urls=["https://x.edu.cn/math/teachers.htm"],
            ),
        ],
    )

    async def resolver(url):
        raise RuntimeError("connection refused")

    probe = _status_probe(resolver)
    summary = await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)

    # both org (probe_failed) and faculty (probe_failed) deferred
    assert summary.org_unit_nodes == 1
    assert summary.faculty_list_nodes == 1
    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        assert all(n.status == NodeStatus.retry for n in nodes)
        assert all(n.next_retry_at is not None for n in nodes)
        assert all(n.last_error == "probe_failed" for n in nodes)
        # neither claimable this run
        assert await h.writer.claim_next(run_id=1, exclude_node_keys=set()) is None
    await _close(h)


async def test_load_seed_nodes_transient_defer_does_not_redefer_on_recovery(tmp_path):
    """下次 load_seed_nodes(模拟 --resume)re-probe 命中 200:不再延迟。
    既有 retry 节点的 next_retry_at 不被刷新(无 fresh defer),等原延迟到期后
    claim_next 自然重抓。
    """
    h = await _writer(tmp_path)
    university = UniversitySeed(
        name="测试大学",
        url="https://x.edu.cn",
        org_unit_listing_urls=["https://x.edu.cn/schools.htm"],
        org_units=[],
    )

    states = [502]

    async def resolver(url):
        return url, states[0]

    probe = _status_probe(resolver)
    await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode))).scalar_one()
        assert node.status == NodeStatus.retry
        first_defer = node.next_retry_at
        assert first_defer is not None

    # gateway recovers — re-probe returns 200
    states[0] = 200
    await load_seed_nodes(university, h, _settings(), run_id=1, status_probe=probe)
    async with h.session_factory() as s:
        node = (await s.execute(select(GraphNode))).scalar_one()
        # no fresh defer: next_retry_at unchanged, status still retry (preserved)
        assert node.next_retry_at == first_defer
        assert node.status == NodeStatus.retry
    await _close(h)
