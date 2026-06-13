"""DBWriter -- the single writer. One coroutine drains an asyncio.Queue and runs
each command (a closure over the worker's own AsyncSession), commits serially,
and resolves the caller's Future. claim_next is an atomic SELECT+UPDATE in that
same session, so the single-claimer + single-writer invariant makes it race-free
(spec section 5). Write exceptions are NOT swallowed: they roll back and
propagate to the caller via the Future.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import or_, select

from dext.storage.models import (
    CrawlRun,
    EdgeType,
    ExtractionAttempt,
    ExtractionFailure,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    OrgUnit,
    PageCache,
    UniversityMeta,
    utcnow_iso,
)

_TERMINAL = {NodeStatus.done, NodeStatus.failed, NodeStatus.skipped}


# --- Input specs (write-API DTOs; produced by SP6) ---
@dataclass
class OrgUnitSpec:
    name: str
    url: str
    kind: str = "college"
    status: str = "pending"
    discovered_from_url: str | None = None


@dataclass
class NodeSpec:
    node_key: str
    type: NodeType
    url: str
    org_unit_id: int | None = None
    org_unit_name: str | None = None
    status: NodeStatus = NodeStatus.pending
    priority_score: float = 0.0
    base_priority: float = 0.0
    confidence: float | None = None
    depth: int = 0
    max_attempts: int = 3
    run_id: int | None = None
    metadata: dict | None = None


@dataclass
class PageCachePayload:
    url: str  # identity URL (synthetic URL for form pagination) -- the cache key
    final_url: str | None = None
    status_code: int | None = None
    text_snapshot: str | None = None
    links: list | None = None
    link_signals: list | None = None
    block_reason: str | None = None
    html_snapshot: str | None = None
    content_hash: str | None = None
    title: str | None = None
    fetch_action: dict | None = None
    snapshot_encoding: str = "utf-8"


@dataclass
class ClaimedNode:
    """Detached snapshot returned by claim_next (safe to use after commit)."""

    id: int
    node_key: str
    type: str
    url: str
    org_unit_id: int | None
    org_unit_name: str | None
    depth: int
    attempt_count: int
    priority_score: float
    content_hash: str | None
    metadata: dict | None


class DBWriter:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()

    # --- machinery ---
    async def run(self) -> None:
        async with self._session_factory() as session:
            while True:
                item = await self._queue.get()
                if item is None:  # stop sentinel
                    break
                op, fut = item
                try:
                    result = await op(session)
                    await session.commit()
                    if not fut.cancelled():
                        fut.set_result(result)
                except Exception as exc:  # noqa: BLE001 -- propagate, don't swallow
                    await session.rollback()
                    if not fut.cancelled():
                        fut.set_exception(exc)

    async def _run(self, op: Callable[[object], Awaitable]):
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((op, fut))
        return await fut

    async def stop(self) -> None:
        await self._queue.put(None)

    # --- graph commands ---
    async def upsert_org_unit(self, spec: OrgUnitSpec) -> int:
        return await self._run(lambda s: _upsert_org_unit(s, spec))

    async def upsert_node(self, spec: NodeSpec) -> int:
        return await self._run(lambda s: _upsert_node(s, spec))

    async def add_edge(self, from_id, to_id, edge_type: EdgeType, *, confidence=None, metadata=None) -> int:
        return await self._run(lambda s: _add_edge(s, from_id, to_id, edge_type, confidence, metadata))

    async def mark_node(self, node_id, status: NodeStatus, *, last_error=None,
                        content_hash=None, next_retry_at=None, attempt_inc=False) -> None:
        return await self._run(
            lambda s: _mark_node(s, node_id, status, last_error, content_hash, next_retry_at, attempt_inc)
        )

    async def claim_next(self, *, run_id, exclude_node_keys=None, types=None, now=None) -> ClaimedNode | None:
        return await self._run(lambda s: _claim_next(s, run_id, exclude_node_keys, types, now))

    # --- page cache / extraction / run-meta commands ---
    async def save_page_cache(self, payload: "PageCachePayload") -> str:
        return await self._run(lambda s: _save_page_cache(s, payload))

    async def record_extraction_attempt(self, *, graph_node_id, attempt=1, status="running",
                                        prompt_hash=None, input_cache_url=None) -> int:
        return await self._run(
            lambda s: _record_attempt(s, graph_node_id, attempt, status, prompt_hash, input_cache_url)
        )

    async def finish_extraction_attempt(self, attempt_id, *, status, raw_output_preview=None, failure_type=None) -> None:
        return await self._run(
            lambda s: _finish_attempt(s, attempt_id, status, raw_output_preview, failure_type)
        )

    async def record_extraction_failure(self, *, failure_type, resolver=None,
                                        raw_arguments_preview=None, professor_name_hint=None, source_url=None) -> int:
        return await self._run(
            lambda s: _record_failure(s, failure_type, resolver, raw_arguments_preview, professor_name_hint, source_url)
        )

    async def start_run(self, *, mode, settings=None) -> int:
        return await self._run(lambda s: _start_run(s, mode, settings))

    async def finish_run(self, run_id, *, status, summary=None) -> None:
        return await self._run(lambda s: _finish_run(s, run_id, status, summary))

    async def update_university_status(self, status) -> None:
        return await self._run(lambda s: _update_university_status(s, status))


# --- command implementations (module-level; take the worker's session) ---
async def _upsert_org_unit(session, spec: OrgUnitSpec) -> int:
    existing = (await session.execute(select(OrgUnit).where(OrgUnit.name == spec.name))).scalar_one_or_none()
    if existing is not None:
        if not existing.url:
            existing.url = spec.url
        if not existing.kind:
            existing.kind = spec.kind
        if not existing.discovered_from_url:
            existing.discovered_from_url = spec.discovered_from_url
        await session.flush()
        return existing.id
    ou = OrgUnit(
        name=spec.name, url=spec.url, kind=spec.kind,
        status=spec.status, discovered_from_url=spec.discovered_from_url,
    )
    session.add(ou)
    await session.flush()
    return ou.id


async def _upsert_node(session, spec: NodeSpec) -> int:
    existing = (await session.execute(select(GraphNode).where(GraphNode.node_key == spec.node_key))).scalar_one_or_none()
    if existing is not None:
        # Re-discovery: never reset status/attempts. Fill missing ownership, take
        # the higher priority, shallow-merge metadata.
        existing.priority_score = max(existing.priority_score, spec.priority_score)
        existing.base_priority = max(existing.base_priority, spec.base_priority)
        if existing.org_unit_id is None and spec.org_unit_id is not None:
            existing.org_unit_id = spec.org_unit_id
        if not existing.org_unit_name and spec.org_unit_name:
            existing.org_unit_name = spec.org_unit_name
        if existing.confidence is None and spec.confidence is not None:
            existing.confidence = spec.confidence
        if spec.metadata:
            existing.metadata_json = {**(existing.metadata_json or {}), **spec.metadata}
        await session.flush()
        return existing.id
    node = GraphNode(
        node_key=spec.node_key, type=spec.type, url=spec.url,
        org_unit_id=spec.org_unit_id, org_unit_name=spec.org_unit_name,
        status=spec.status, priority_score=spec.priority_score, base_priority=spec.base_priority,
        confidence=spec.confidence, depth=spec.depth, max_attempts=spec.max_attempts,
        run_id=spec.run_id, metadata_json=spec.metadata,
    )
    session.add(node)
    await session.flush()
    return node.id


async def _add_edge(session, from_id, to_id, edge_type: EdgeType, confidence, metadata) -> int:
    existing = (
        await session.execute(
            select(GraphEdge).where(
                GraphEdge.from_node_id == from_id,
                GraphEdge.to_node_id == to_id,
                GraphEdge.edge_type == edge_type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    edge = GraphEdge(
        from_node_id=from_id, to_node_id=to_id, edge_type=edge_type,
        confidence=confidence, metadata_json=metadata,
    )
    session.add(edge)
    await session.flush()
    return edge.id


async def _mark_node(session, node_id, status: NodeStatus, last_error, content_hash, next_retry_at, attempt_inc) -> None:
    node = (await session.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
    node.status = status
    if last_error is not None:
        node.last_error = last_error
    if content_hash is not None:
        node.content_hash = content_hash
    if next_retry_at is not None:
        node.next_retry_at = next_retry_at
    if attempt_inc:
        node.attempt_count += 1
    if status in _TERMINAL:
        node.completed_at = utcnow_iso()
    await session.flush()


async def _claim_next(session, run_id, exclude_node_keys, types, now) -> ClaimedNode | None:
    now = now or utcnow_iso()
    stmt = (
        select(GraphNode)
        .where(
            GraphNode.status.in_([NodeStatus.pending, NodeStatus.retry]),
            GraphNode.attempt_count < GraphNode.max_attempts,
            or_(GraphNode.next_retry_at.is_(None), GraphNode.next_retry_at <= now),
        )
        .order_by(GraphNode.priority_score.desc(), GraphNode.id.asc())
    )
    if types:
        stmt = stmt.where(GraphNode.type.in_([t.value for t in types]))
    if exclude_node_keys:
        stmt = stmt.where(GraphNode.node_key.notin_(list(exclude_node_keys)))
    node = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if node is None:
        return None
    node.status = NodeStatus.in_progress
    node.claimed_at = now
    node.run_id = run_id
    node.attempt_count += 1  # a claim IS an attempt (gates max_attempts)
    await session.flush()
    return ClaimedNode(
        id=node.id, node_key=node.node_key, type=node.type, url=node.url,
        org_unit_id=node.org_unit_id, org_unit_name=node.org_unit_name,
        depth=node.depth, attempt_count=node.attempt_count,
        priority_score=node.priority_score, content_hash=node.content_hash,
        metadata=node.metadata_json,
    )


async def _save_page_cache(session, payload: PageCachePayload) -> str:
    existing = (await session.execute(select(PageCache).where(PageCache.url == payload.url))).scalar_one_or_none()
    target = existing or PageCache(url=payload.url)
    target.final_url = payload.final_url
    target.status_code = payload.status_code
    target.text_snapshot = payload.text_snapshot
    target.links_json = payload.links
    target.link_signals_json = payload.link_signals
    target.block_reason = payload.block_reason
    target.html_snapshot = payload.html_snapshot
    target.content_hash = payload.content_hash
    target.title = payload.title
    target.fetch_action_json = payload.fetch_action
    target.snapshot_encoding = payload.snapshot_encoding
    if existing is None:
        session.add(target)
    await session.flush()
    return target.url


async def _record_attempt(session, graph_node_id, attempt, status, prompt_hash, input_cache_url) -> int:
    row = ExtractionAttempt(
        graph_node_id=graph_node_id, attempt=attempt, status=status,
        prompt_hash=prompt_hash, input_cache_url=input_cache_url,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _finish_attempt(session, attempt_id, status, raw_output_preview, failure_type) -> None:
    row = (await session.execute(select(ExtractionAttempt).where(ExtractionAttempt.id == attempt_id))).scalar_one()
    row.status = status
    if raw_output_preview is not None:
        row.raw_output_preview = raw_output_preview
    if failure_type is not None:
        row.failure_type = failure_type
    row.finished_at = utcnow_iso()
    await session.flush()


async def _record_failure(session, failure_type, resolver, raw_arguments_preview, professor_name_hint, source_url) -> int:
    row = ExtractionFailure(
        failure_type=failure_type, resolver=resolver,
        raw_arguments_preview=raw_arguments_preview,
        professor_name_hint=professor_name_hint, source_url=source_url,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _start_run(session, mode, settings) -> int:
    run = CrawlRun(mode=mode, started_at=utcnow_iso(), status="running", settings_json=settings)
    session.add(run)
    await session.flush()
    meta = (await session.execute(select(UniversityMeta))).scalars().first()
    if meta is not None:
        meta.last_run_id = run.id
    await session.flush()
    return run.id


async def _finish_run(session, run_id, status, summary) -> None:
    run = (await session.execute(select(CrawlRun).where(CrawlRun.id == run_id))).scalar_one()
    run.status = status
    run.finished_at = utcnow_iso()
    if summary is not None:
        run.summary_json = summary
    await session.flush()


async def _update_university_status(session, status) -> None:
    meta = (await session.execute(select(UniversityMeta))).scalars().first()
    if meta is not None:
        meta.crawl_status = status
    await session.flush()
