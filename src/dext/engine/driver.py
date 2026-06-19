"""SP6 crawl engine driver."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy import func, select

from dext.bridge.probe import (
    DEAD_CAT,
    PROBE_FAILED,
    PROBE_TIMEOUT,
    RATE_LIMITED_CAT,
    TRANSIENT_CAT,
)
from dext.bridge.queue import JobContext
from dext.engine.handlers import HandlerDeps, fetch_action_from_metadata, identity_url_for
from dext.engine.retry import assess_terminal_unavailable_page, classify_fetch_failure
from dext.engine.seeds import _apply_probe_defer
from dext.engine.workers import DecideTask, ExtractTask, InFlightTracker, decision_worker, extract_worker
from dext.page import PageSnapshot, build_snapshot
from dext.storage.models import GraphNode, NodeStatus, NodeType, PageCache, Professor, ProfessorAffiliation
from dext.storage.writer import PageCachePayload
from dext.types import FetchResult

logger = logging.getLogger(__name__)


@dataclass
class RateThrottle:
    """Minimal global backoff on HTTP 429.

    On ``on_rate_limited()`` sets an ``until`` timestamp with exponential
    backoff (capped). ``maybe_wait()`` is called by the single-threaded driver
    loop before each ``claim_next`` — since the driver is the single fetch gate,
    throttling the loop throttles everything. Conservative defaults: base 2s,
    cap 60s; intentionally not a token bucket.
    """

    base_seconds: float = 2.0
    max_seconds: float = 60.0
    _until: float = 0.0
    _attempt: int = 0

    async def on_rate_limited(self) -> None:
        self._attempt = min(self._attempt + 1, 8)
        delay = min(self.base_seconds * (2 ** (self._attempt - 1)), self.max_seconds)
        self._until = time.monotonic() + delay
        logger.info("rate_limited backoff attempt=%s delay=%.1fs", self._attempt, delay)

    async def on_success(self) -> None:
        if self._attempt:
            self._attempt = 0
            self._until = 0.0

    async def maybe_wait(self) -> None:
        remaining = self._until - time.monotonic()
        if remaining > 0:
            logger.info("rate_limited waiting %.2fs before next claim", remaining)
            await asyncio.sleep(remaining)


@dataclass
class CrawlSummary:
    fetched: int = 0
    fetch_failed: int = 0
    dispatched: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    retry: int = 0
    pending: int = 0
    in_progress: int = 0
    professors: int = 0
    status: str = "running"
    node_status_counts: dict[str, int] = field(default_factory=dict)

    def asdict(self) -> dict:
        return {
            "fetched": self.fetched,
            "fetch_failed": self.fetch_failed,
            "dispatched": self.dispatched,
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "retry": self.retry,
            "pending": self.pending,
            "in_progress": self.in_progress,
            "professors": self.professors,
            "status": self.status,
            "node_status_counts": self.node_status_counts,
        }


class CrawlEngine:
    def __init__(
        self,
        storage,
        bridge,
        llm_client,
        settings,
        run_id: int,
        *,
        university_name: str = "",
        decision_center=None,
        redirect_guard=None,
        status_probe=None,
        rate_throttle: RateThrottle | None = None,
        org_unit_ids: set[int] | None = None,
    ) -> None:
        self.storage = storage
        self.bridge = bridge
        self.llm_client = llm_client
        self.settings = settings
        self.run_id = run_id
        self.university_name = university_name
        self.decision_center = decision_center
        self.redirect_guard = redirect_guard
        self.status_probe = status_probe
        self._throttle = rate_throttle or RateThrottle()
        self.org_unit_ids = set(org_unit_ids or set())
        self.decision_queue: asyncio.Queue = asyncio.Queue()
        self.extract_queue: asyncio.Queue = asyncio.Queue()
        self._attempted_this_run: set[str] = set()
        self._decision_tracker = InFlightTracker()
        self._extract_tracker = InFlightTracker()
        self._summary = CrawlSummary()

    async def run(self) -> CrawlSummary:
        decision_worker_tasks = [
            asyncio.create_task(
                decision_worker(
                    f"decision-{i}",
                    self.decision_queue,
                    self._decision_tracker,
                    deps_factory=self._deps_factory,
                )
            )
            for i in range(max(1, self.settings.decision_workers))
        ]
        extract_worker_tasks = [
            asyncio.create_task(
                extract_worker(
                    f"extract-{i}",
                    self.extract_queue,
                    self.storage,
                    self.llm_client,
                    self.settings,
                    self._extract_tracker,
                )
            )
            for i in range(max(1, self.settings.extract_workers))
        ]
        if not self.org_unit_ids:
            await self.storage.writer.update_university_status("in_progress")
        try:
            await self._driver_loop()
            await self.decision_queue.join()
            await self.extract_queue.join()
            final = await self._build_summary()
            await self.storage.writer.finish_run(self.run_id, status=final.status, summary=final.asdict())
            if not self.org_unit_ids:
                await self.storage.writer.update_university_status(
                    "completed" if final.status == "completed" else "failed"
                )
            return final
        finally:
            for _ in decision_worker_tasks:
                await self.decision_queue.put(None)
            for _ in extract_worker_tasks:
                await self.extract_queue.put(None)
            await self.decision_queue.join()
            await self.extract_queue.join()
            await asyncio.gather(*decision_worker_tasks, *extract_worker_tasks)

    async def _driver_loop(self) -> None:
        while True:
            await self._throttle.maybe_wait()
            node = await self.storage.writer.claim_next(
                run_id=self.run_id,
                exclude_node_keys=self._attempted_this_run,
                org_unit_ids=self.org_unit_ids,
            )
            if node is None:
                if (
                    self.decision_queue.empty()
                    and self.extract_queue.empty()
                    and self._decision_tracker.in_flight == 0
                    and self._extract_tracker.in_flight == 0
                ):
                    return
                await asyncio.sleep(0.05)
                continue
            self._attempted_this_run.add(node.node_key)
            await self._handle_claimed_node(node)

    async def _handle_claimed_node(self, node) -> None:
        fetch_identity = identity_url_for(node)
        action = fetch_action_from_metadata(node.metadata)
        context = JobContext(
            university_name=self.university_name,
            agent_state=node.type,
            intent=_intent_for(node.type),
            parent_url=(node.metadata or {}).get("parent_url", ""),
            depth=node.depth,
            org_unit_name=node.org_unit_name or "",
            hints=[],
        )
        cache = await self.storage.writer.get_page_cache(fetch_identity)
        if cache is None:
            if not await self._prefetch_probe(node):
                return  # 探针判定为阻塞风险(5xx/redirect-loop/dead),已 defer/skip,不交给前端
            result = await self.bridge.fetch(url=node.url, identity_url=fetch_identity, action=action, context=context)
            self._summary.fetched += 1
            await self._process_fresh_result(node, action, result)
            return
        await self._process_cached_result(node, action, cache)

    async def _prefetch_probe(self, node) -> bool:
        """claim 后、bridge.fetch 前的探针兜底:所有交给前端的 URL 都过这里。

        覆盖入图前探针够不到的场景——resume 时库里已存在的旧坏节点(如 redirect-loop
        死链)。探针失败快速 degrade,绝不阻塞单线程浏览器抓取器:
        - 5xx / probe_failed(redirect-loop/不可达) → defer(retry + next_retry_at),
          本 run claim_next 跳过(配合 _attempted_this_run exclude),下次 --resume 重探;
        - probe_timeout(后端旁路超时,真人浏览器常能打开) → **不阻断**,照常 fetch;
        - dead(404/410) → skipped,不 fetch;
        - 429 → 触发全局退避后仍 fetch(429 浏览器快速返回,不阻塞);
        - ok / 未注入探针 / 非 http(s) URL → 正常 fetch。
        返回 True = 继续 fetch,False = 已 defer/skip,跳过 fetch。
        """
        if self.status_probe is None:
            return True
        parts = urlsplit(node.url)
        if parts.scheme not in ("http", "https"):
            return True
        verdict = await self.status_probe.probe(node.url)
        if verdict.category == DEAD_CAT:
            await self.storage.writer.mark_node(
                node.id, NodeStatus.skipped, last_error=verdict.reason
            )
            await self.storage.writer.record_extraction_failure(
                failure_type=f"prefetch_probe:{verdict.reason}",
                resolver="dropped",
                source_url=node.url,
            )
            self._summary.skipped += 1
            logger.info("prefetch probe dead url=%s status=%s", node.url, verdict.status_code)
            return False
        if verdict.category == PROBE_TIMEOUT:
            # 探针超时 = 后端旁路够不到(慢宿主/UA 拦截),不代表浏览器也够不到。
            # 不 defer、不 skip → 照常交前端 fetch,避免慢宿主子树被 defer 24h 后
            # /jobs/next 长期 204("fetch 没有新任务")。
            logger.info("prefetch probe timeout url=%s → proceeding to fetch", node.url)
            return True
        if verdict.category in (TRANSIENT_CAT, PROBE_FAILED):
            reason = verdict.reason or ("transient" if verdict.category == TRANSIENT_CAT else "probe_failed")
            await _apply_probe_defer(self.storage, node.id, {"probe_defer_reason": reason})
            await self.storage.writer.record_extraction_failure(
                failure_type=f"prefetch_probe:{reason}",
                resolver="retry",
                source_url=node.url,
            )
            self._summary.retry += 1
            logger.info(
                "prefetch probe defer url=%s category=%s status=%s",
                node.url, verdict.category, verdict.status_code,
            )
            return False
        if verdict.category == RATE_LIMITED_CAT:
            await self._throttle.on_rate_limited()
            logger.info("prefetch probe rate_limited url=%s status=%s", node.url, verdict.status_code)
        return True

    async def _process_fresh_result(self, node, action, result: FetchResult) -> None:
        if result.block_reason:
            self._summary.fetch_failed += 1
            await self._save_failed_page_cache(result)
            decision = classify_fetch_failure(result.block_reason, status_code=result.status_code)
            await self.storage.writer.record_extraction_failure(
                failure_type=f"fetch:{result.block_reason}",
                resolver=decision.resolver,
                raw_arguments_preview=None,
                source_url=result.identity_url,
            )
            if decision.resolver == "rate_limited":
                await self._throttle.on_rate_limited()
            await self.storage.writer.mark_node(node.id, decision.status, last_error=decision.last_error)
            return

        snapshot = build_snapshot(result.html, result.identity_url, result.final_url, result.title)
        terminal_unavailable = assess_terminal_unavailable_page(snapshot, status_code=result.status_code)
        if not terminal_unavailable:
            await self._throttle.on_success()
        await self.storage.writer.save_page_cache(
            PageCachePayload(
                url=result.identity_url,
                final_url=result.final_url,
                status_code=result.status_code,
                text_snapshot=snapshot.text_snapshot,
                links=snapshot.links,
                link_signals=[signal.__dict__ for signal in snapshot.link_signals],
                block_reason=f"terminal_unavailable:{terminal_unavailable}" if terminal_unavailable else None,
                html_snapshot=result.html,
                content_hash=snapshot.content_hash,
                title=snapshot.title,
                fetch_action=action.__dict__ if action is not None else None,
            )
        )
        if terminal_unavailable:
            reason = f"terminal_unavailable:{terminal_unavailable}"
            self._summary.fetch_failed += 1
            await self.storage.writer.record_extraction_failure(
                failure_type=f"fetch:{reason}",
                resolver="dropped",
                raw_arguments_preview=None,
                source_url=result.identity_url,
            )
            await self.storage.writer.mark_node(
                node.id,
                NodeStatus.skipped,
                last_error=reason,
                content_hash=snapshot.content_hash,
            )
            return

        await self._dispatch_snapshot(
            node,
            snapshot,
            raw_html=result.html,
            pagination_states=result.pagination_states,
        )

    async def _process_cached_result(self, node, action, cache: PageCache) -> None:
        if cache.html_snapshot:
            snapshot = build_snapshot(
                cache.html_snapshot,
                cache.url,
                cache.final_url or cache.url,
                cache.title or "",
            )
            terminal_unavailable = assess_terminal_unavailable_page(snapshot, status_code=cache.status_code)
            if terminal_unavailable:
                reason = f"terminal_unavailable:{terminal_unavailable}"
                self._summary.fetch_failed += 1
                await self.storage.writer.record_extraction_failure(
                    failure_type=f"fetch:{reason}",
                    resolver="dropped",
                    raw_arguments_preview=None,
                    source_url=cache.url,
                )
                await self.storage.writer.mark_node(
                    node.id,
                    NodeStatus.skipped,
                    last_error=reason,
                    content_hash=snapshot.content_hash,
                )
                return

            if NodeType(node.type) == NodeType.detail_url:
                logger.info("dispatching cached detail node_id=%s url=%s", node.id, cache.url)
            await self._dispatch_snapshot(
                node,
                snapshot,
                raw_html=cache.html_snapshot,
                pagination_states=[],
            )
            return

        reason = cache.block_reason or "cached_no_html"
        self._summary.fetch_failed += 1
        decision = classify_fetch_failure(reason, status_code=cache.status_code)
        await self.storage.writer.record_extraction_failure(
            failure_type=f"fetch:{reason}",
            resolver=decision.resolver,
            raw_arguments_preview=None,
            source_url=cache.url,
        )
        if decision.resolver == "rate_limited":
            await self._throttle.on_rate_limited()
        await self.storage.writer.mark_node(node.id, decision.status, last_error=decision.last_error)

    async def _dispatch_snapshot(
        self,
        node,
        snapshot: PageSnapshot,
        *,
        raw_html: str,
        pagination_states: list,
    ) -> None:
        if NodeType(node.type) == NodeType.detail_url:
            await self.extract_queue.put(
                ExtractTask(
                    node_id=node.id,
                    node_key=node.node_key,
                    snapshot=snapshot,
                    org_unit_id=node.org_unit_id,
                    org_unit_name=node.org_unit_name or "",
                    attempt_count=node.attempt_count,
                )
            )
        else:
            await self.decision_queue.put(
                DecideTask(
                    node=node,
                    snapshot=snapshot,
                    raw_html=raw_html,
                    reported_pagination_states=pagination_states,
                )
            )
        self._summary.dispatched += 1

    def _deps_factory(self, task: DecideTask) -> HandlerDeps:
        return HandlerDeps(
            storage=self.storage,
            llm_client=self.llm_client,
            settings=self.settings,
            run_id=self.run_id,
            university_name=self.university_name,
            extract_queue=self.extract_queue,
            reported_pagination_states=task.reported_pagination_states,
            raw_html=task.raw_html,
            decision_center=self.decision_center,
            redirect_guard=self.redirect_guard,
            status_probe=self.status_probe,
        )

    async def _save_failed_page_cache(self, result) -> None:
        await self.storage.writer.save_page_cache(
            PageCachePayload(
                url=result.identity_url,
                final_url=result.final_url,
                status_code=result.status_code,
                block_reason=result.block_reason,
                html_snapshot=result.html,
                title=result.title,
            )
        )

    async def _build_summary(self) -> CrawlSummary:
        async with self.storage.session() as session:
            node_stmt = select(GraphNode.status, func.count()).group_by(GraphNode.status)
            prof_stmt = select(func.count()).select_from(Professor)
            if self.org_unit_ids:
                node_stmt = node_stmt.where(GraphNode.org_unit_id.in_(list(self.org_unit_ids)))
                prof_stmt = (
                    select(func.count(func.distinct(Professor.id)))
                    .select_from(Professor)
                    .join(ProfessorAffiliation, ProfessorAffiliation.professor_id == Professor.id)
                    .where(ProfessorAffiliation.org_unit_id.in_(list(self.org_unit_ids)))
                )
            rows = (await session.execute(node_stmt)).all()
            counts = {str(status): count for status, count in rows}
            prof_count = (await session.execute(prof_stmt)).scalar_one()
        self._summary.node_status_counts = counts
        self._summary.done = counts.get(NodeStatus.done.value, 0)
        self._summary.failed = counts.get(NodeStatus.failed.value, 0)
        self._summary.skipped = counts.get(NodeStatus.skipped.value, 0)
        self._summary.retry = counts.get(NodeStatus.retry.value, 0)
        self._summary.pending = counts.get(NodeStatus.pending.value, 0)
        self._summary.in_progress = counts.get(NodeStatus.in_progress.value, 0)
        self._summary.professors = prof_count
        blocking = self._summary.failed + self._summary.retry + self._summary.pending + self._summary.in_progress
        self._summary.status = "completed" if blocking == 0 else "failed"
        return self._summary


def _intent_for(node_type: str) -> str:
    try:
        t = NodeType(node_type)
    except ValueError:
        return node_type
    if t == NodeType.detail_url:
        return "detail_extract"
    if t == NodeType.org_listing_url:
        return "org_listing"
    if t == NodeType.org_unit:
        return "org_unit"
    return "faculty_list"
