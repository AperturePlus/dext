"""SP6 retry and terminal-state mapping."""

from __future__ import annotations

from dataclasses import dataclass

from dext.storage.models import NodeStatus


@dataclass(frozen=True)
class RetryDecision:
    status: NodeStatus
    last_error: str | None = None
    resolver: str | None = None
    retryable: bool = False


def classify_fetch_failure(block_reason: str | None) -> RetryDecision:
    reason = (block_reason or "fetch_failed").strip() or "fetch_failed"
    lowered = reason.lower()
    if lowered == "human_skip":
        return RetryDecision(NodeStatus.skipped, last_error=reason, resolver="dropped")
    if lowered in {"invalid_url", "bad_url"}:
        return RetryDecision(NodeStatus.failed, last_error=reason, resolver="dropped")
    return RetryDecision(NodeStatus.retry, last_error=reason, resolver="retry", retryable=True)


def classify_extraction_failure(result, *, extract_attempt_index: int, invalid_json_max_retry: int) -> RetryDecision:
    failure_type = result.failure_type or "extraction_failed"
    if failure_type == "invalid_json":
        if extract_attempt_index < invalid_json_max_retry:
            return RetryDecision(NodeStatus.retry, last_error="invalid_json", resolver="retry", retryable=True)
        return RetryDecision(
            NodeStatus.retry,
            last_error="invalid_json_retry_exhausted",
            resolver="retry",
            retryable=True,
        )
    if failure_type == "no_structured_data":
        if result.recoverable:
            return RetryDecision(
                NodeStatus.retry,
                last_error="rich_detail_no_structured_data",
                resolver="retry",
                retryable=True,
            )
        return RetryDecision(NodeStatus.failed, last_error="no_structured_data", resolver="dropped")
    return RetryDecision(NodeStatus.failed, last_error=failure_type, resolver="dropped")
