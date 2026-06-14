from dext.bridge.decision import DecisionCenter, PendingDecision


def _decision(did="d1"):
    return PendingDecision(id=did, kind="detail_failures", org_unit_name="计算机学院",
                           failure_count=3, sample_urls=["https://x/1", "https://x/2"],
                           suggested_action="switch_failed_to_human")


async def test_set_and_get_decision():
    dc = DecisionCenter()
    assert dc.current() is None
    d = _decision(); dc.set_decision(d)
    assert dc.current() is d
    assert d.status == "pending"


async def test_resolve_fires_async_callback_and_clears_slot():
    dc = DecisionCenter()
    seen = []

    async def cb(decision, action):
        seen.append((decision.id, action))

    dc.on_resolve(cb)
    d = _decision(); dc.set_decision(d)
    assert await dc.resolve("d1", "switch_failed_to_human") is True
    assert seen == [("d1", "switch_failed_to_human")]
    assert dc.current() is None
    assert d.status == "resolved" and d.action == "switch_failed_to_human"
    assert d.resolved_at is not None


async def test_resolve_unknown_id_ignored():
    dc = DecisionCenter(); dc.set_decision(_decision())
    assert await dc.resolve("other", "x") is False
    assert dc.current() is not None


async def test_sync_callback_supported():
    dc = DecisionCenter(); seen = []
    dc.on_resolve(lambda d, a: seen.append(a))
    dc.set_decision(_decision())
    await dc.resolve("d1", "act")
    assert seen == ["act"]
