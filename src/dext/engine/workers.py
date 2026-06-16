"""Concurrent LLM extraction workers for detail nodes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from dext.exclusions import is_valid_exclusion_reason
from dext.llm import OrgUnitContext, extract_professors
from dext.page.links import PageSnapshot
from dext.storage.models import NodeStatus
from dext.storage.writer import ClaimedNode
from dext.engine.retry import classify_extraction_failure
from dext.types import ProfessorPayload

logger = logging.getLogger(__name__)


@dataclass
class ExtractTask:
    node_id: int
    node_key: str
    snapshot: PageSnapshot
    org_unit_id: int | None
    org_unit_name: str
    attempt_count: int


@dataclass
class DecideTask:
    node: ClaimedNode
    snapshot: PageSnapshot
    raw_html: str
    reported_pagination_states: list


class ExtractionTracker:
    def __init__(self) -> None:
        self.in_flight = 0


def _payloads_with_system_homepage(payloads: list[ProfessorPayload], homepage: str) -> list[ProfessorPayload]:
    return [replace(payload, homepage=homepage) for payload in payloads]


async def llm_worker(
    name: str,
    queue: asyncio.Queue,
    storage,
    llm_client,
    settings,
    tracker: ExtractionTracker,
    *,
    deps_factory=None,
) -> None:
    # Deferred import: handlers imports ExtractTask from this module, so a top-level
    # `from dext.engine.handlers import dispatch` would create an import cycle.
    from dext.engine.handlers import dispatch

    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        tracker.in_flight += 1  # no await between get() and increment — closes the DONE-race window
        try:
            if isinstance(task, DecideTask):
                await dispatch(task.node, task.snapshot, deps_factory(task))
            else:
                await process_extract_task(task, storage, llm_client, settings)
        finally:
            tracker.in_flight -= 1
            queue.task_done()


async def process_extract_task(task: ExtractTask, storage, llm_client, settings) -> None:
    org_unit_id = task.org_unit_id
    org_unit_name = task.org_unit_name or ""
    attempt_id = await storage.writer.record_extraction_attempt(
        graph_node_id=task.node_id,
        attempt=task.attempt_count,
        input_cache_url=task.snapshot.url,
    )
    try:
        result = await extract_professors(
            task.snapshot,
            OrgUnitContext(org_unit_id=org_unit_id, org_unit_name=org_unit_name),
            client=llm_client,
            attempt=max(task.attempt_count - 1, 0),
        )
        if result.payloads:
            payloads = _payloads_with_system_homepage(result.payloads, task.snapshot.url)
            save_result = await storage.writer.save_professors(
                payloads,
                org_unit_id=org_unit_id,
                org_unit_name=org_unit_name,
            )
            if getattr(save_result, "save_errors", 0):
                await storage.writer.finish_extraction_attempt(
                    attempt_id,
                    status="failed",
                    raw_output_preview=result.raw_preview,
                    failure_type="save_error",
                )
                await storage.writer.record_extraction_failure(
                    failure_type="save_error",
                    resolver="dropped",
                    raw_arguments_preview=result.raw_preview,
                    source_url=task.snapshot.url,
                )
                await storage.writer.mark_node(task.node_id, NodeStatus.failed, last_error="save_error")
                return
            await storage.writer.finish_extraction_attempt(
                attempt_id,
                status="succeeded",
                raw_output_preview=result.raw_preview,
            )
            await storage.writer.mark_node(task.node_id, NodeStatus.done, content_hash=task.snapshot.content_hash)
            return

        if result.exclusion_reason and is_valid_exclusion_reason(result.exclusion_reason):
            await storage.writer.finish_extraction_attempt(
                attempt_id,
                status="skipped",
                raw_output_preview=result.raw_preview,
                failure_type="excluded",
            )
            await storage.writer.mark_node(
                task.node_id,
                NodeStatus.skipped,
                last_error=f"excluded:{result.exclusion_reason}",
            )
            return

        decision = classify_extraction_failure(
            result,
            extract_attempt_index=max(task.attempt_count - 1, 0),
            invalid_json_max_retry=settings.invalid_json_max_retry,
        )
        await storage.writer.finish_extraction_attempt(
            attempt_id,
            status="retry" if decision.status == NodeStatus.retry else "failed",
            raw_output_preview=result.raw_preview,
            failure_type=result.failure_type,
        )
        await storage.writer.record_extraction_failure(
            failure_type=result.failure_type,
            resolver=decision.resolver,
            raw_arguments_preview=result.raw_preview,
            source_url=task.snapshot.url,
        )
        await storage.writer.mark_node(task.node_id, decision.status, last_error=decision.last_error)
    except Exception as exc:  # noqa: BLE001 -- persist the failure and let the run continue
        logger.exception("extraction failed for node %s", task.node_key)
        await storage.writer.finish_extraction_attempt(
            attempt_id,
            status="failed",
            raw_output_preview=repr(exc)[:500],
            failure_type="save_error",
        )
        await storage.writer.record_extraction_failure(
            failure_type="save_error",
            resolver="dropped",
            raw_arguments_preview=repr(exc)[:500],
            source_url=task.snapshot.url,
        )
        await storage.writer.mark_node(task.node_id, NodeStatus.failed, last_error="save_error")
