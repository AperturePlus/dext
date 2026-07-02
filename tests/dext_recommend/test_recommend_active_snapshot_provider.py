from __future__ import annotations

import asyncio

import pytest

from dext_recommend import RecommendSettings, RecommendationRuntimeError
from dext_recommend.adapters.active_snapshot import LiveActiveSnapshotProvider
from dext_recommend.readiness import ReadinessReport
from tests.dext_recommend._recfixtures import snapshot


class FakeReadiness:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def check(self):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def report(snap=None, *, ready=True):
    return ReadinessReport(
        ready=ready,
        snapshot=snap,
        errors=(),
        payload_coverage={},
    )


async def test_start_caches_only_ready_snapshot():
    snap = snapshot()
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(snap)), RecommendSettings(),
    )
    await provider.start()
    assert provider.get_snapshot() is snap
    assert provider.last_report().ready is True


async def test_start_without_ready_snapshot_fails():
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(None, ready=False)), RecommendSettings(),
    )
    with pytest.raises(RecommendationRuntimeError) as raised:
        await provider.start()
    assert raised.value.code == "readiness_failed"


async def test_failed_refresh_preserves_last_good_snapshot():
    first = snapshot("b-1")
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(first), report(None, ready=False)),
        RecommendSettings(),
    )
    await provider.start()
    await provider._refresh_once()
    assert provider.get_snapshot() is first


async def test_successful_refresh_replaces_snapshot():
    first = snapshot("b-1")
    second = snapshot("b-2")
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(first), report(second)), RecommendSettings(),
    )
    await provider.start()
    await provider._refresh_once()
    assert provider.get_snapshot() is second


async def test_stale_and_closed_provider_fail_closed(monkeypatch):
    clock = iter((100.0, 102.1))
    monkeypatch.setattr(
        "dext_recommend.adapters.active_snapshot.monotonic", lambda: next(clock),
    )
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(snapshot())),
        RecommendSettings(runtime_snapshot_max_age=2.0),
    )
    await provider.start()
    assert provider.get_snapshot() is None
    await provider.aclose()
    assert provider.get_snapshot() is None


async def test_aclose_cancels_refresh_task_and_is_idempotent():
    provider = LiveActiveSnapshotProvider(
        FakeReadiness(report(snapshot())),
        RecommendSettings(runtime_refresh_interval=3600),
    )
    await provider.start()
    provider.start_refresh()
    task = provider._refresh_task
    await asyncio.sleep(0)
    await provider.aclose()
    await provider.aclose()
    assert task.done()
    assert task.cancelled()
