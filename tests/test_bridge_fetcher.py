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
    task = asyncio.ensure_future(b.fetch(url="https://x/list", context=_ctx()))
    job = await _await_job(b)
    assert job.url == "https://x/list"
    mojibake = "计算机学院".encode("utf-8").decode("latin-1")
    assert b.complete(job.id, html=f"<h1>{mojibake}</h1>", final_url="https://x/list?p=1", title="教师") is True
    result = await task
    assert isinstance(result, FetchResult)
    assert result.identity_url == "https://x/list"        # defaults to url
    assert result.final_url == "https://x/list?p=1"
    assert "计算机学院" in result.html                      # mojibake repaired
    assert result.block_reason is None
    assert b.stats().completed == 1


async def test_identity_url_preserved_as_cache_key():
    b = _bridge()
    task = asyncio.ensure_future(
        b.fetch(url="https://x/list?__ycl_page=2", identity_url="https://x/list#syn2", context=_ctx()))
    job = await _await_job(b)
    b.complete(job.id, html="x", final_url="https://x/list?p=2", title="t")
    assert (await task).identity_url == "https://x/list#syn2"


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
    result = await b.fetch(url="https://x/list", context=_ctx())
    assert result.block_reason == "timeout"
    assert result.final_url == "https://x/list"
    assert b.stats().failed == 1
    assert b.current_job() is None


async def test_override_swaps_url_keeps_job_and_future():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x/wrong", context=_ctx()))
    job = await _await_job(b)
    updated = b.override(job.id, "https://x/right")
    assert updated is not None and updated.id == job.id and updated.url == "https://x/right"
    assert b.current_job().url == "https://x/right"
    b.complete(job.id, html="", final_url="https://x/right", title="")
    result = await task
    assert result.requested_url == "https://x/right"
