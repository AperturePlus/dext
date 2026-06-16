import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext.bridge.decision import DecisionCenter, PendingDecision
from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.queue import JobContext
from dext.bridge.server import create_app


@pytest.fixture
async def harness():
    bridge = HumanFetcherBridge(SimpleNamespace(fetch_timeout_seconds=60))
    dc = DecisionCenter()
    client = TestClient(TestServer(create_app(bridge, dc)))
    await client.start_server()
    try:
        yield SimpleNamespace(client=client, bridge=bridge, dc=dc)
    finally:
        await client.close()


async def _poll_next(client):
    for _ in range(200):
        resp = await client.get("/api/jobs/next")
        if resp.status == 200:
            return await resp.json()
        assert resp.status == 204
        await asyncio.sleep(0)
    raise AssertionError("no job offered")


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload))


async def test_next_is_204_when_empty(harness):
    resp = await harness.client.get("/api/jobs/next")
    assert resp.status == 204


async def test_full_round_trip_complete(harness):
    ctx = JobContext(university_name="清华大学", intent="org_listing")
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=ctx))
    job = await _poll_next(harness.client)
    assert job["url"] == "https://x/list"
    assert job["status"] == "assigned"
    assert job["context"]["university_name"] == "清华大学"
    assert job["action"] is None and job["identity_url"] is None
    mojibake = "计算机学院".encode("utf-8").decode("latin-1")
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                       {"html": f"<h1>{mojibake}</h1>", "url": "https://x/list?p=1",
                        "title": "教师", "pagination_states": []})
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok" and body["next_job"] is None
    result = await task
    assert result.final_url == "https://x/list?p=1"
    assert "计算机学院" in result.html


async def test_complete_with_explicit_port_final_url_resolves_as_invalid_url(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(
        harness.client,
        f"/api/jobs/{job['id']}/complete",
        {"html": "<h1>bad</h1>", "url": "https://x.edu.cn:8080/list", "title": "", "pagination_states": []},
    )
    assert resp.status == 200
    assert (await resp.json())["status"] == "ok"
    result = await task
    assert result.block_reason == "invalid_url:explicit_port"


async def test_single_in_flight_returns_204(harness):
    t1 = asyncio.ensure_future(harness.bridge.fetch(url="u1", context=JobContext()))
    t2 = asyncio.ensure_future(harness.bridge.fetch(url="u2", context=JobContext()))
    job = await _poll_next(harness.client)
    assert (await harness.client.get("/api/jobs/next")).status == 204
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "u1", "title": "", "pagination_states": []})
    await t1
    job2 = await _poll_next(harness.client)
    await _post(harness.client, f"/api/jobs/{job2['id']}/complete",
                {"html": "", "url": "u2", "title": "", "pagination_states": []})
    await t2


async def test_fail_path_sets_block_reason(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="u", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/fail", {"message": "页面打不开"})
    assert resp.status == 200 and (await resp.json())["status"] == "ok"
    assert (await task).block_reason == "页面打不开"


async def test_skip_path_is_human_skip(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="u", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await harness.client.post(f"/api/jobs/{job['id']}/skip")
    assert resp.status == 200
    assert (await task).block_reason == "human_skip"


async def test_override_swaps_url(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/wrong", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/override", {"new_url": "https://x/right"})
    assert resp.status == 200
    assert (await resp.json())["url"] == "https://x/right"
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/right", "title": "", "pagination_states": []})
    assert (await task).requested_url == "https://x/right"


async def test_override_rejects_explicit_port_url(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/wrong", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/override", {"new_url": "https://x:443/right"})
    assert resp.status == 204
    current = await harness.client.get("/api/status")
    body = await current.json()
    assert body["current_job"]["url"] == "https://x/wrong"
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/wrong", "title": "", "pagination_states": []})
    assert (await task).requested_url == "https://x/wrong"


async def test_override_unknown_job_is_204(harness):
    resp = await _post(harness.client, "/api/jobs/nope/override", {"new_url": "https://x/y"})
    assert resp.status == 204


async def test_stale_complete_is_ignored_200(harness):
    resp = await _post(harness.client, "/api/jobs/ghost/complete",
                       {"html": "", "url": "u", "title": "", "pagination_states": []})
    assert resp.status == 200
    assert (await resp.json())["status"] == "ignored"


async def test_status_reports_counts_and_utf8(harness):
    task = asyncio.ensure_future(
        harness.bridge.fetch(url="https://x/list", context=JobContext(university_name="北京大学")))
    await _poll_next(harness.client)
    resp = await harness.client.get("/api/status")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"
    raw = await resp.text()
    assert "北京大学" in raw          # ensure_ascii=False
    data = json.loads(raw)
    assert data["queue"]["assigned"] == 1
    assert data["current_job"]["url"] == "https://x/list"
    assert isinstance(data["server_uptime_seconds"], (int, float))
    assert data["pending_decision"] is None
    await _post(harness.client, f"/api/jobs/{data['current_job']['id']}/complete",
                {"html": "", "url": "https://x/list", "title": "", "pagination_states": []})
    await task


async def test_decision_get_204_then_resolve(harness):
    assert (await harness.client.get("/api/decision")).status == 204
    seen = []
    harness.dc.on_resolve(lambda d, a: seen.append(a))
    harness.dc.set_decision(PendingDecision(id="d1", kind="detail_failures", org_unit_name="物理学院",
                                            failure_count=3, sample_urls=["https://x/1"],
                                            suggested_action="switch_failed_to_human"))
    got = await harness.client.get("/api/decision")
    assert got.status == 200
    body = await got.json()
    assert body["id"] == "d1" and body["org_unit_name"] == "物理学院"
    assert body["failure_count"] == 3 and body["resolved_at"] is None
    resp = await _post(harness.client, "/api/decision/d1/resolve", {"action": "switch_failed_to_human"})
    assert resp.status == 200 and (await resp.json())["status"] == "ok"
    assert seen == ["switch_failed_to_human"]
    assert (await harness.client.get("/api/decision")).status == 204


async def test_pagination_states_parsed_into_result(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=JobContext()))
    job = await _poll_next(harness.client)
    state = {"kind": "form_submit", "state_id": "form:f:p:2", "label": "f 第 2 页", "page_index": 2,
             "total_pages": 5, "form_name": "f", "fields": {"p": "2"}, "submit": True,
             "synthetic_url": "https://x/list?__ycl_page=2", "url": "https://x/list"}
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/list", "title": "", "pagination_states": [state, {"bad": 1}]})
    result = await task
    assert len(result.pagination_states) == 1          # malformed one dropped
    assert result.pagination_states[0].page_index == 2
    assert result.pagination_states[0].total_pages == 5


async def test_explicit_port_pagination_states_are_dropped(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=JobContext()))
    job = await _poll_next(harness.client)
    good = {"kind": "form_submit", "state_id": "form:f:p:2", "label": "f 第 2 页", "page_index": 2,
            "total_pages": 5, "form_name": "f", "fields": {"p": "2"}, "submit": True,
            "synthetic_url": "https://x/list?__ycl_page=2", "url": "https://x/list"}
    bad = {**good, "state_id": "form:f:p:3", "page_index": 3,
           "synthetic_url": "https://x.edu.cn:443/list?__ycl_page=3", "url": "https://x/list"}
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/list", "title": "", "pagination_states": [bad, good]})
    result = await task
    assert [s.page_index for s in result.pagination_states] == [2]
