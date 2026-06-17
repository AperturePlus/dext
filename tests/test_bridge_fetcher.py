import asyncio
from types import SimpleNamespace

from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.queue import JobContext
from dext.types import FetchResult


def _bridge(timeout=60):
    return HumanFetcherBridge(SimpleNamespace(fetch_timeout_seconds=timeout))


def _ctx():
    return JobContext(university_name="清华大学", intent="org_listing")


async def _await_job(bridge):
    for _ in range(200):
        job = bridge.next_job()
        if job is not None:
            return job
        await asyncio.sleep(0)
    raise AssertionError("job was never queued")


async def test_fetch_resolves_on_complete_with_repaired_html():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/list", context=_ctx()))
    job = await _await_job(b)
    assert job.url == "https://x.edu.cn/list"
    mojibake = "计算机学院".encode("utf-8").decode("latin-1")
    assert b.complete(job.id, html=f"<h1>{mojibake}</h1>", final_url="https://x.edu.cn/list?p=1", title="教师") is True
    result = await task
    assert isinstance(result, FetchResult)
    assert result.identity_url == "https://x.edu.cn/list"        # defaults to url
    assert result.final_url == "https://x.edu.cn/list?p=1"
    assert "计算机学院" in result.html                      # mojibake repaired
    assert result.block_reason is None
    assert b.stats().completed == 1


async def test_complete_with_explicit_port_final_url_fails_job():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/list", context=_ctx()))
    job = await _await_job(b)
    assert b.complete(job.id, html="<h1>bad</h1>", final_url="https://x.edu.cn:443/list", title="t") is True
    result = await task
    assert result.block_reason == "invalid_url:explicit_port"
    assert result.final_url == "https://x.edu.cn/list"
    assert b.stats().failed == 1


async def test_identity_url_preserved_as_cache_key():
    b = _bridge()
    task = asyncio.ensure_future(
        b.fetch(url="https://x.edu.cn/list?__ycl_page=2", identity_url="https://x.edu.cn/list#syn2", context=_ctx()))
    job = await _await_job(b)
    b.complete(job.id, html="x", final_url="https://x.edu.cn/list?p=2", title="t")
    assert (await task).identity_url == "https://x.edu.cn/list#syn2"


async def test_single_in_flight_second_next_is_none():
    b = _bridge()
    t1 = asyncio.ensure_future(b.fetch(url="u1", context=_ctx()))
    t2 = asyncio.ensure_future(b.fetch(url="u2", context=_ctx()))
    job = await _await_job(b)
    assert b.next_job() is None
    b.complete(job.id, html="", final_url="u1", title=""); await t1
    job2 = await _await_job(b)
    b.complete(job2.id, html="", final_url="u2", title=""); await t2


async def test_fail_sets_block_reason_from_message():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.fail(job.id, "WAF 拦截")
    assert (await task).block_reason == "WAF 拦截"
    assert b.stats().failed == 1


async def test_fail_empty_message_defaults_human_failed():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.fail(job.id, "   ")
    assert (await task).block_reason == "human_failed"


async def test_skip_is_terminal_human_skip():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.skip(job.id)
    assert (await task).block_reason == "human_skip"
    assert b.stats().skipped == 1


async def test_late_complete_after_resolution_ignored():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    assert b.complete(job.id, html="", final_url="u", title="") is True
    await task
    assert b.complete(job.id, html="", final_url="u", title="") is False   # stale
    assert b.stats().completed == 1


async def test_unknown_job_id_ignored_everywhere():
    b = _bridge()
    assert b.complete("nope", html="", final_url="u", title="") is False
    assert b.fail("nope", "x") is False
    assert b.skip("nope") is False
    assert b.override("nope", "u2") is None


async def test_timeout_returns_block_reason_timeout_and_counts_failed():
    b = _bridge(timeout=0.02)
    b.record_frontend_heartbeat(owner_tab_id="tab1", url="https://assistant.local")
    result = await b.fetch(url="https://x.edu.cn/list", context=_ctx())
    assert result.block_reason == "timeout"
    assert result.final_url == "https://x.edu.cn/list"
    assert b.stats().failed == 1
    assert b.current_job() is None


async def test_timeout_pauses_while_frontend_heartbeat_is_absent():
    b = _bridge(timeout=0.02)
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/list", context=_ctx()))
    job = await _await_job(b)
    await asyncio.sleep(0.06)
    assert not task.done()
    assert b.current_job() is job
    b.complete(job.id, html="ok", final_url="https://x.edu.cn/list", title="")
    assert (await task).block_reason is None
    assert b.stats().completed == 1
    assert b.stats().failed == 0


async def test_timeout_resumes_after_frontend_heartbeat_returns():
    b = _bridge(timeout=0.02)
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/list", context=_ctx()))
    await _await_job(b)
    await asyncio.sleep(0.03)
    assert not task.done()
    b.record_frontend_heartbeat(owner_tab_id="tab1", url="https://assistant.local")
    result = await asyncio.wait_for(task, timeout=0.2)
    assert result.block_reason == "timeout"
    assert b.stats().failed == 1


async def test_override_swaps_url_keeps_job_and_future():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/wrong", context=_ctx()))
    job = await _await_job(b)
    updated = b.override(job.id, "https://x.edu.cn/right")
    assert updated is not None and updated.id == job.id and updated.url == "https://x.edu.cn/right"
    assert b.current_job().url == "https://x.edu.cn/right"
    b.complete(job.id, html="", final_url="https://x.edu.cn/right", title="")
    result = await task
    assert result.requested_url == "https://x.edu.cn/right"


async def test_override_rejects_explicit_port_url():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/wrong", context=_ctx()))
    job = await _await_job(b)
    assert b.override(job.id, "https://x.edu.cn:8080/right") is None
    assert b.current_job().url == "https://x.edu.cn/wrong"
    b.complete(job.id, html="", final_url="https://x.edu.cn/wrong", title="")
    assert (await task).requested_url == "https://x.edu.cn/wrong"


async def test_fetch_blocks_disallowed_host():
    b = _bridge()
    result = await b.fetch(url="https://evil.com/p", context=_ctx())
    assert result.block_reason == "invalid_url:host_not_allowed"
    assert result.final_url == "https://evil.com/p"
    assert result.requested_url == "https://evil.com/p"
    assert b.next_job() is None
    assert b.stats().failed == 0


async def test_override_rejects_disallowed_host():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x.edu.cn/wrong", context=_ctx()))
    job = await _await_job(b)
    assert b.override(job.id, "https://evil.com/x") is None
    assert b.current_job().url == "https://x.edu.cn/wrong"
    b.complete(job.id, html="", final_url="https://x.edu.cn/wrong", title="")
    assert (await task).requested_url == "https://x.edu.cn/wrong"
