from dext.types import FetchResult, PaginationState


def test_fetch_result_defaults_block_reason_none():
    r = FetchResult(
        identity_url="https://x/list", requested_url="https://x/list",
        final_url="https://x/list?p=2", status_code=None,
        html="<html>中文</html>", title="教师", pagination_states=[],
    )
    assert r.identity_url == "https://x/list"
    assert r.block_reason is None
    assert r.pagination_states == []


def test_fetch_result_carries_states_and_block_reason():
    ps = PaginationState(kind="form_submit", state_id="form:f:p:2", label="f 第 2 页",
                         page_index=2, form_name="f", fields={"p": "2"}, submit=True,
                         synthetic_url="https://x/list?__ycl_page=2", url="https://x/list")
    r = FetchResult(identity_url="i", requested_url="r", final_url="f", status_code=200,
                    html="", title="", pagination_states=[ps], block_reason="timeout")
    assert r.pagination_states[0].page_index == 2
    assert r.block_reason == "timeout"
