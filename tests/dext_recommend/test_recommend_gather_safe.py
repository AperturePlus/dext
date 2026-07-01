import asyncio

import pytest

from dext_recommend.adapters._readback_call import ReadbackCall, gather_safe
from dext_recommend.errors import ErrorSeverity, RecommendationErrorCode
from dext_recommend.ports.release_readback import ReadinessSourceError


async def _ok(value): return value
async def _raise_source(): raise ReadinessSourceError("x", "boom", retryable=True)
async def _raise_runtime(): raise RuntimeError("boom")
async def _slow():
    await asyncio.sleep(10); return "late"


async def test_gather_safe_passes_through_normal_values():
    calls = [
        ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _ok("a")),
        ReadbackCall("ranking", RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE, _ok("v1")),
    ]
    out = await gather_safe(*calls, timeout=5.0)
    assert out == ("a", "v1")


async def test_gather_safe_converts_readiness_source_error():
    calls = [ReadbackCall("vector", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _raise_source())]
    out = await gather_safe(*calls, timeout=5.0)
    assert isinstance(out[0], type(out[0]))  # RecommendationError
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is True


async def test_gather_safe_converts_timeout():
    calls = [ReadbackCall("graph", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _slow())]
    out = await gather_safe(*calls, timeout=0.05)
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is True
    assert "timed out" in out[0].message


async def test_gather_safe_converts_other_exceptions():
    calls = [ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _raise_runtime())]
    out = await gather_safe(*calls, timeout=5.0)
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is False


async def test_gather_safe_passes_none_through():
    calls = [ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _ok(None))]
    out = await gather_safe(*calls, timeout=5.0)
    assert out == (None,)
