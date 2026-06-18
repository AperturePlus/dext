"""SP6 retry and terminal-state mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dext.page.links import PageSnapshot
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
    if lowered in {"human_skip", "wechat_redirect", "offsite_redirect"}:
        return RetryDecision(NodeStatus.skipped, last_error=reason, resolver="dropped")
    if _is_terminal_unavailable_reason(lowered):
        return RetryDecision(NodeStatus.skipped, last_error=reason, resolver="dropped")
    if lowered == "bad_url" or lowered.startswith("invalid_url"):
        return RetryDecision(NodeStatus.failed, last_error=reason, resolver="dropped")
    return RetryDecision(NodeStatus.retry, last_error=reason, resolver="retry", retryable=True)


def _is_terminal_unavailable_reason(reason: str) -> bool:
    return reason.startswith("terminal_unavailable:")


def assess_terminal_unavailable_page(
    snapshot: PageSnapshot,
    *,
    status_code: int | None = None,
) -> str | None:
    """Classify deterministic unavailable pages that should never be retried.

    This is deliberately limited to technical terminal pages, not semantic
    exclusion categories. Faculty-related exclusion remains LLM-owned.
    """
    title = snapshot.title or ""
    text = snapshot.text_snapshot or ""
    haystack = f"{title}\n{text}".strip().lower()
    if status_code == 404:
        return "not_found"
    if not text.strip() and not snapshot.links:
        return "empty_page"
    if len(text) <= 4000 and _has_terminal_unavailable_text(haystack):
        return "not_found"
    if len(text) <= 4000 and _has_removed_text(haystack):
        return "content_removed"
    return None


def _has_terminal_unavailable_text(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\b404\b", text):
        return True
    return any(
        token in text
        for token in (
            "not found",
            "page not found",
            "页面不存在",
            "网页不存在",
            "未找到页面",
            "找不到页面",
            "访问的页面不存在",
            "您访问的页面不存在",
            "信息不存在",
            "该信息不存在",
            "文章不存在",
        )
    )


def _has_removed_text(text: str) -> bool:
    if not text:
        return False
    return any(
        token in text
        for token in (
            "内容已撤销",
            "内容被撤销",
            "该内容已被删除",
            "内容已被删除",
            "文章已被删除",
            "信息已被删除",
            "该信息已删除",
            "已下线",
            "页面已下线",
            "内容已失效",
        )
    )


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
