from __future__ import annotations

import dataclasses

import pytest

from dext_recommend import ConversationContext, ConversationSummary
from dext_recommend.ports import FakeConversationStorePort, TurnSnapshot


def _snapshot(ids=("e1",)):
    return TurnSnapshot(
        build_id="b1", ranking_profile_version="rv",
        generation_profile_version="gp", result_entity_ids=ids,
        intent="new_search", intent_source="explicit",
        sanitized_summary="NLP preference", created_at="2026-07-02T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_context_and_summary_round_trip():
    context = ConversationContext(
        session_id="s1", turn_id="t1", intent_source="explicit", intent="new_search"
    )
    summary = ConversationSummary("s1", "t1", "NLP", "2026-07-02T00:00:00Z")
    store = FakeConversationStorePort(summaries={("s1", "t1"): summary})
    await store.save_turn("s1", "t1", context, _snapshot())
    assert await store.load_context("s1", "t1") == context
    assert await store.load_summary("s1", "t1") is summary


@pytest.mark.asyncio
async def test_prior_ids_are_deduplicated_in_saved_order():
    store = FakeConversationStorePort()
    context = ConversationContext()
    await store.save_turn("s1", "t1", context, _snapshot(("e1", "e2")))
    await store.save_turn("s1", "t2", context, _snapshot(("e2", "e3")))
    assert await store.list_prior_entity_ids("s1") == ("e1", "e2", "e3")


def test_turn_snapshot_has_no_sensitive_fields_and_is_immutable():
    fields = {field.name for field in dataclasses.fields(TurnSnapshot)}
    assert fields.isdisjoint({"contacts", "fact_bundle", "student_context", "query_text"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        _snapshot().intent = "changed"
