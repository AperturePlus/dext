from __future__ import annotations

import sqlite3

import pytest

from dext_recommend import FakeProfessorFactPort, ProfessorDetail, ViewerPermissions
from dext_recommend.core.detail_fetch import fetch_details
from dext_recommend.core._resilience import RecommendExecutionContext

from tests.dext_recommend._recfixtures import professor_details_case, snapshot


async def test_fetch_details_fans_out_one_call_per_entity():
    details = professor_details_case("happy")
    port = FakeProfessorFactPort(details=details)
    out, failed = await fetch_details(
        snapshot(), port, ["e_cv_strong", "e_missing"],
        include_contacts=False, viewer_permissions=ViewerPermissions(),
        concurrency=8, ctx=RecommendExecutionContext(),
    )
    assert out["e_cv_strong"] is not None
    assert isinstance(out["e_cv_strong"], ProfessorDetail)
    assert out["e_missing"] is None  # missing detail -> None, not crash
    # KeyError/LookupError missing is NOT an operational failure
    assert failed == set()
    assert len(port.get_detail_calls) == 2
    ids = [c["entity_id"] for c in port.get_detail_calls]
    assert set(ids) == {"e_cv_strong", "e_missing"}


async def test_fetch_details_respects_concurrency_cap():
    details = professor_details_case("happy")
    port = FakeProfessorFactPort(details=details)
    # 10 entities, concurrency 3 -> never more than 3 in flight
    eids = [f"e{i}" for i in range(10)]
    # give every entity the same detail to avoid KeyError; copy dict
    port = FakeProfessorFactPort(details={eid: details["e_cv_strong"] for eid in eids})
    out, failed = await fetch_details(
        snapshot(), port, eids, include_contacts=False,
        viewer_permissions=ViewerPermissions(), concurrency=3,
        ctx=RecommendExecutionContext(),
    )
    assert len(out) == 10
    assert len(port.get_detail_calls) == 10


async def test_fetch_details_uses_single_entity_signature():
    details = professor_details_case("happy")
    port = FakeProfessorFactPort(details={"e_cv_strong": details["e_cv_strong"]})
    await fetch_details(
        snapshot(), port, ["e_cv_strong"], include_contacts=False,
        viewer_permissions=ViewerPermissions(), concurrency=8,
        ctx=RecommendExecutionContext(),
    )
    # each call is for one entity_id, never a list
    for call in port.get_detail_calls:
        assert isinstance(call["entity_id"], str)


async def test_fetch_details_prefers_optional_batch_getter():
    detail = professor_details_case("happy")["e_cv_strong"]

    class BatchPort:
        def __init__(self):
            self.get_detail_calls = []
            self.get_details_calls = []

        async def get_details(self, snapshot, entity_ids, include_contacts, viewer_permissions):
            self.get_details_calls.append(list(entity_ids))
            return {"e_cv_strong": detail}

        async def get_detail(self, snapshot, entity_id, include_contacts, viewer_permissions):
            self.get_detail_calls.append(entity_id)
            raise AssertionError("single detail path should not be used")

        async def hydrate(self, snapshot, entity_ids):
            return {}

    port = BatchPort()
    out, failed = await fetch_details(
        snapshot(), port, ["e_cv_strong", "e_missing"],
        include_contacts=False, viewer_permissions=ViewerPermissions(),
        concurrency=8, ctx=RecommendExecutionContext(),
    )
    assert out["e_cv_strong"] is detail
    assert out["e_missing"] is None
    assert failed == set()
    assert port.get_details_calls == [["e_cv_strong", "e_missing"]]
    assert port.get_detail_calls == []


async def test_fetch_details_batch_retryable_failure_does_not_fall_back_to_single():
    class FailingBatchPort:
        def __init__(self):
            self.get_details_calls = []
            self.get_detail_calls = []

        async def get_details(self, snapshot, entity_ids, include_contacts, viewer_permissions):
            self.get_details_calls.append(list(entity_ids))
            raise sqlite3.OperationalError("database is locked")

        async def get_detail(self, snapshot, entity_id, include_contacts, viewer_permissions):
            self.get_detail_calls.append(entity_id)
            raise AssertionError("single detail path should not be used after batch failure")

        async def hydrate(self, snapshot, entity_ids):
            return {}

    port = FailingBatchPort()
    out, failed = await fetch_details(
        snapshot(), port, ["e1", "e2"],
        include_contacts=False, viewer_permissions=ViewerPermissions(),
        concurrency=8, ctx=RecommendExecutionContext(),
    )

    assert out == {"e1": None, "e2": None}
    assert failed == {"e1", "e2"}
    assert port.get_details_calls == [["e1", "e2"], ["e1", "e2"]]
    assert port.get_detail_calls == []
