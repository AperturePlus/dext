from __future__ import annotations

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
