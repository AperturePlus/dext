"""SP6 crawl engine driver."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import func, select

from dext.bridge.queue import JobContext
from dext.engine.handlers import HandlerDeps, fetch_action_from_metadata, identity_url_for
from dext.engine.retry import assess_terminal_unavailable_page, classify_fetch_failure
from dext.engine.workers import DecideTask, ExtractionTracker, ExtractTask, llm_worker
from dext.page import build_snapshot
from dext.storage.models import GraphNode, NodeStatus, NodeType, PageCache, Professor, ProfessorAffiliation
from dext.storage.writer import PageCachePayload


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
        self.org_unit_ids = set(org_unit_ids or set())
        self.extract_queue: asyncio.Queue = asyncio.Queue()
        self._attempted_this_run: set[str] = set()
        self._tracker = ExtractionTracker()
        self._summary = CrawlSummary()

    async def run(self) -> CrawlSummary:
        worker_tasks = [
            asyncio.create_task(
                llm_worker(
                    f"llm-{i}",
                    self.extract_queue,
                    self.storage,
                    self.llm_client,
                    self.settings,
                    self._tracker,
                    deps_factory=self._deps_factory,
                )
            )
            for i in range(max(1, self.settings.llm_workers))
        ]
        if not self.org_unit_ids:
            await self.storage.writer.update_university_status("in_progress")
        try:
            await self._driver_loop()
            await self.extract_queue.join()
            final = await self._build_summary()
            await self.storage.writer.finish_run(self.run_id, status=final.status, summary=final.asdict())
            if not self.org_unit_ids:
                await self.storage.writer.update_university_status(
                    "completed" if final.status == "completed" else "failed"
                )
            return final
        finally:
            for _ in worker_tasks:
                await self.extract_queue.put(None)
            await self.extract_queue.join()
            await asyncio.gather(*worker_tasks)

    async def _driver_loop(self) -> None:
        while True:
            node = await self.storage.writer.claim_next(
                run_id=self.run_id,
                exclude_node_keys=self._attempted_this_run,
                org_unit_ids=self.org_unit_ids,
            )
            if node is None:
                if self.extract_queue.empty() and self._tracker.in_flight == 0:
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
        result = await self.bridge.fetch(url=node.url, identity_url=fetch_identity, action=action, context=context)
        self._summary.fetched += 1

        if result.block_reason:
            self._summary.fetch_failed += 1
            await self._save_failed_page_cache(result)
            decision = classify_fetch_failure(result.block_reason)
            await self.storage.writer.record_extraction_failure(
                failure_type=f"fetch:{result.block_reason}",
                resolver=decision.resolver,
                raw_arguments_preview=None,
                source_url=result.identity_url,
            )
            await self.storage.writer.mark_node(node.id, decision.status, last_error=decision.last_error)
            return

        snapshot = build_snapshot(result.html, result.identity_url, result.final_url, result.title)
        terminal_unavailable = assess_terminal_unavailable_page(snapshot, status_code=result.status_code)
        await self.storage.writer.save_page_cache(
            PageCachePayload(
                url=result.identity_url,
                final_url=result.final_url,
                status_code=result.status_code,
                text_snapshot=snapshot.text_snapshot,
                links=snapshot.links,
                link_signals=[s.__dict__ for s in snapshot.link_signals],
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
            await self.extract_queue.put(
                DecideTask(
                    node=node,
                    snapshot=snapshot,
                    raw_html=result.html,
                    reported_pagination_states=result.pagination_states,
                )
            )
        self._summary.dispatched += 1
        # Return immediately → the driver loop claims the next pending node and keeps fetching.

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
