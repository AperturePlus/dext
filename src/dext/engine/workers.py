"""Decision + extraction worker pools for the crawl engine."""

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


class InFlightTracker:
    def __init__(self) -> None:
        self.in_flight = 0


ExtractionTracker = InFlightTracker


def _payloads_with_system_homepage(payloads: list[ProfessorPayload], homepage: str) -> list[ProfessorPayload]:
    return [replace(payload, homepage=homepage) for payload in payloads]


async def decision_worker(
    name: str,
    queue: asyncio.Queue,
    tracker: InFlightTracker,
    *,
    deps_factory,
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
            if not isinstance(task, DecideTask):
                raise TypeError(f"decision_worker expected DecideTask, got {type(task).__name__}")
            await dispatch(task.node, task.snapshot, deps_factory(task))
        finally:
            tracker.in_flight -= 1
            queue.task_done()


async def extract_worker(
    name: str,
    queue: asyncio.Queue,
    storage,
    llm_client,
    settings,
    tracker: InFlightTracker,
) -> None:
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        tracker.in_flight += 1  # no await between get() and increment — closes the DONE-race window
        try:
            if not isinstance(task, ExtractTask):
                raise TypeError(f"extract_worker expected ExtractTask, got {type(task).__name__}")
            await process_extract_task(task, storage, llm_client, settings)
        finally:
            tracker.in_flight -= 1
            queue.task_done()


async def process_extract_task(task: ExtractTask, storage, llm_client, settings) -> None:
    org_unit_id = task.org_unit_id
    org_unit_name = task.org_unit_name or ""

    # Per-URL extraction dedup (node_key regression safety net): if the same page
    # was already successfully extracted under another graph node, do NOT re-invoke
    # the LLM — rebuild/reuse the prior result instead. The page cache already
    # prevented an HTTP re-fetch; this prevents the LLM re-fetch (the token waste).
    reused_node_id = await storage.writer.find_done_detail_node_for_url(
        task.snapshot.url, exclude_node_id=task.node_id
    )
    if reused_node_id is not None:
        attempt_id = await storage.writer.record_extraction_attempt(
            graph_node_id=task.node_id,
            attempt=task.attempt_count,
            input_cache_url=task.snapshot.url,
        )
        await storage.writer.finish_extraction_attempt(
            attempt_id, status="skipped", failure_type="duplicate_url",
        )
        await storage.writer.mark_node(
            task.node_id,
            NodeStatus.done,
            last_error=f"duplicate_url_reused:{reused_node_id}",
            content_hash=task.snapshot.content_hash,
        )
        logger.info(
            "extract skipped duplicate_url node_id=%s url=%s reused_node=%s",
            task.node_id,
            task.snapshot.url,
            reused_node_id,
        )
        return

    attempt_id = await storage.writer.record_extraction_attempt(
        graph_node_id=task.node_id,
        attempt=task.attempt_count,
        input_cache_url=task.snapshot.url,
    )
    logger.info(
        "extract attempt started node_id=%s attempt=%s url=%s",
        task.node_id,
        task.attempt_count,
        task.snapshot.url,
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
                logger.info("extract attempt failed node_id=%s reason=save_error", task.node_id)
                return
            await storage.writer.finish_extraction_attempt(
                attempt_id,
                status="succeeded",
                raw_output_preview=result.raw_preview,
            )
            await storage.writer.mark_node(task.node_id, NodeStatus.done, content_hash=task.snapshot.content_hash)
            logger.info(
                "extract attempt succeeded node_id=%s payloads=%d",
                task.node_id,
                len(result.payloads),
            )
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
            logger.info(
                "extract attempt skipped node_id=%s reason=excluded:%s",
                task.node_id,
                result.exclusion_reason,
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
        logger.info(
            "extract attempt completed node_id=%s status=%s reason=%s",
            task.node_id,
            decision.status,
            decision.last_error,
        )
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
        logger.info("extract attempt failed node_id=%s reason=exception", task.node_id)
