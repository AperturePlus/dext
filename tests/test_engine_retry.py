from types import SimpleNamespace

from dext.engine.retry import assess_terminal_unavailable_page, classify_extraction_failure, classify_fetch_failure
from dext.page.links import PageSnapshot
from dext.storage.models import NodeStatus


def test_fetch_failure_mapping():
    assert classify_fetch_failure("human_skip").status == NodeStatus.skipped
    assert classify_fetch_failure("wechat_redirect").status == NodeStatus.skipped
    assert classify_fetch_failure("wechat_redirect").resolver == "dropped"
    assert classify_fetch_failure("offsite_redirect").status == NodeStatus.skipped
    assert classify_fetch_failure("offsite_redirect").resolver == "dropped"
    assert classify_fetch_failure("offsite_redirect").retryable is False
    assert classify_fetch_failure("invalid_url").status == NodeStatus.failed
    assert classify_fetch_failure("invalid_url:explicit_port").status == NodeStatus.failed
    timeout = classify_fetch_failure("timeout")
    assert timeout.status == NodeStatus.retry
    assert timeout.retryable is True
    terminal = classify_fetch_failure("terminal_unavailable:not_found")
    assert terminal.status == NodeStatus.skipped
    assert terminal.retryable is False


def test_invalid_json_retry_then_exhaustion():
    result = SimpleNamespace(failure_type="invalid_json")
    retry = classify_extraction_failure(result, extract_attempt_index=0, invalid_json_max_retry=2)
    exhausted = classify_extraction_failure(result, extract_attempt_index=2, invalid_json_max_retry=2)
    assert retry.status == NodeStatus.retry
    assert retry.last_error == "invalid_json"
    assert exhausted.status == NodeStatus.retry
    assert exhausted.last_error == "invalid_json_retry_exhausted"


def test_no_structured_data_mapping():
    rich = SimpleNamespace(failure_type="no_structured_data", recoverable=True)
    sparse = SimpleNamespace(failure_type="no_structured_data", recoverable=False)
    assert classify_extraction_failure(rich, extract_attempt_index=0, invalid_json_max_retry=2).status == NodeStatus.retry
    assert classify_extraction_failure(sparse, extract_attempt_index=0, invalid_json_max_retry=2).status == NodeStatus.failed


def _snap(text: str, *, title: str = "", links: list[str] | None = None) -> PageSnapshot:
    return PageSnapshot(
        url="https://x.edu.cn/p",
        final_url="https://x.edu.cn/p",
        title=title,
        text_snapshot=text,
        links=links or [],
        link_signals=[],
        content_hash="h",
    )


def test_terminal_unavailable_page_classifier():
    assert assess_terminal_unavailable_page(_snap("404 Not Found")) == "not_found"
    assert assess_terminal_unavailable_page(_snap("该内容已被删除")) == "content_removed"
    assert assess_terminal_unavailable_page(_snap("", links=[])) == "empty_page"
    assert assess_terminal_unavailable_page(_snap("张三，教授，研究方向：财务管理。")) is None
    assert assess_terminal_unavailable_page(_snap("欢迎访问本站。"), status_code=404) == "not_found"
