from __future__ import annotations

from dext_recommend.runtime import LiveRecommendationRuntime


class AsyncCloser:
    def __init__(self, name, events, *, fail=False):
        self.name = name
        self.events = events
        self.fail = fail

    async def aclose(self):
        self.events.append(self.name)
        if self.fail:
            raise RuntimeError("close failed")


class DriverCloser:
    def __init__(self, events):
        self.events = events

    async def close(self):
        self.events.append("neo4j")


async def test_runtime_close_is_reverse_order_idempotent_and_failure_isolated():
    events = []
    runtime = LiveRecommendationRuntime(
        core=object(),
        conversation=object(),
        auxiliary_generation=object(),
        readiness=AsyncCloser("readiness", events),
        generation_profile=object(),
        _aux_llm=AsyncCloser("llm_aux", events),
        _core_llm=AsyncCloser("llm_core", events, fail=True),
        _embedding=AsyncCloser("embedding", events),
        _vector=AsyncCloser("qdrant", events),
        _neo4j_driver=DriverCloser(events),
    )
    await runtime.aclose()
    await runtime.aclose()
    assert events == [
        "readiness", "llm_aux", "llm_core", "embedding", "qdrant", "neo4j",
    ]
