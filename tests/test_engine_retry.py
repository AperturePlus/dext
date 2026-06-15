from types import SimpleNamespace

from dext.engine.retry import classify_extraction_failure, classify_fetch_failure
from dext.storage.models import NodeStatus


def test_fetch_failure_mapping():
    assert classify_fetch_failure("human_skip").status == NodeStatus.skipped
    assert classify_fetch_failure("invalid_url").status == NodeStatus.failed
    timeout = classify_fetch_failure("timeout")
    assert timeout.status == NodeStatus.retry
    assert timeout.retryable is True


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
