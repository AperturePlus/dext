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

from sqlalchemy import delete, func, or_, select, update

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

    async def claim_next(
        self,
        *,
        run_id,
        exclude_node_keys=None,
        types=None,
        now=None,
        org_unit_ids=None,
    ) -> ClaimedNode | None:
        return await self._run(lambda s: _claim_next(s, run_id, exclude_node_keys, types, now, org_unit_ids))

    async def count_subtree_facet_nodes(self, org_unit_id) -> int:
        return await self._run(lambda s: _count_subtree_facet_nodes(s, org_unit_id))

    async def find_done_detail_node_for_url(self, url: str, *, exclude_node_id: int) -> int | None:
        """Return the id of a *different* `done` detail_url node sharing this URL,
        or None. Used by the extract path to skip a redundant LLM call when the same
        page was already successfully extracted under another graph node (the
        node_key-dedup regression safety net)."""
        return await self._run(lambda s: _find_done_detail_for_url(s, url, exclude_node_id))

    # --- page cache / extraction / run-meta commands ---
    async def save_page_cache(self, payload: "PageCachePayload") -> str:
        return await self._run(lambda s: _save_page_cache(s, payload))

    async def get_page_cache(self, url: str) -> PageCache | None:
        return await self._run(lambda s: _get_page_cache(s, url))

    async def save_professors(self, payloads, *, org_unit_id, org_unit_name):
        from dext.storage.dedup import save_professors as _save_professors

        return await self._run(
            lambda s: _save_professors(s, payloads, org_unit_id=org_unit_id, org_unit_name=org_unit_name)
        )

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

    async def start_run(self, *, mode, settings=None, backup_path=None) -> int:
        return await self._run(lambda s: _start_run(s, mode, settings, backup_path))

    async def finish_run(self, run_id, *, status, summary=None) -> None:
        return await self._run(lambda s: _finish_run(s, run_id, status, summary))

    async def update_org_unit_status(self, org_unit_id, status) -> None:
        return await self._run(lambda s: _update_org_unit_status(s, org_unit_id, status))

    async def update_university_status(self, status) -> None:
        return await self._run(lambda s: _update_university_status(s, status))

    async def reset_org_unit_subtree(self, org_unit_id) -> dict:
        """Rebuild mode (`--reset -oid X`): delete the org_unit's discovered
        subtree (detail / faculty_followup / pagination nodes), reset its
        entry-point nodes (org_unit + faculty_list_url) to pending, and clear
        their page_cache so they are re-fetched. Edges, extraction attempts,
        and page_cache rows touching deleted nodes are removed. The org_unit
        row itself is kept and its status reset to pending. Professors are
        intentionally preserved (re-extraction is idempotent via dedup)."""
        return await self._run(lambda s: _reset_org_unit_subtree(s, org_unit_id))

    async def reset_bad_detail_snapshots(self) -> dict:
        """Bad-snapshot mode (`--reset` without -oid): find detail_url leaf
        nodes whose page_cache has an empty/whitespace text_snapshot (or was
        flagged terminal_unavailable:empty_page) — the snapshot-capture
        regression footprint — reset them to pending and delete the stale
        page_cache so they are re-fetched. Returns a count summary."""
        return await self._run(_reset_bad_detail_snapshots)


# --- command implementations (module-level; take the worker's session) ---
async def _upsert_org_unit(session, spec: OrgUnitSpec) -> int:
    existing = (await session.execute(select(OrgUnit).where(OrgUnit.url == spec.url))).scalar_one_or_none()
    if existing is not None:
        if existing.name != spec.name:
            existing.name = spec.name
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
        if existing.org_unit_name is None and spec.org_unit_name is not None:
            existing.org_unit_name = spec.org_unit_name
        if existing.confidence is None and spec.confidence is not None:
            existing.confidence = spec.confidence
        if spec.metadata:
            metadata = dict(existing.metadata_json or {})
            for key, value in spec.metadata.items():
                metadata.setdefault(key, value)
            existing.metadata_json = metadata
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


async def _get_page_cache(session, url: str) -> PageCache | None:
    return (await session.execute(select(PageCache).where(PageCache.url == url))).scalar_one_or_none()


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


async def _claim_next(session, run_id, exclude_node_keys, types, now, org_unit_ids) -> ClaimedNode | None:
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
    if org_unit_ids:
        stmt = stmt.where(GraphNode.org_unit_id.in_(list(org_unit_ids)))
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


async def _count_subtree_facet_nodes(session, org_unit_id) -> int:
    stmt = (
        select(func.count())
        .select_from(GraphNode)
        .where(
            GraphNode.org_unit_id == org_unit_id,
            GraphNode.type.in_(
                [NodeType.faculty_list_url, NodeType.faculty_followup_url, NodeType.pagination_url]
            ),
        )
    )
    return (await session.execute(stmt)).scalar_one()


def _scheme_variants(url: str) -> list[str]:
    """Return the URL plus its http/https counterpart, so a page discovered as
    `http://` matches its `https://` twin (and vice-versa). Same-site pages are
    frequently linked under both schemes, which historically created duplicate
    detail nodes that re-invoked the LLM on an already-extracted page."""
    if url.startswith("https://"):
        return [url, "http://" + url[len("https://"):]]
    if url.startswith("http://"):
        return [url, "https://" + url[len("http://"):]]
    return [url]


async def _find_done_detail_for_url(session, url: str, exclude_node_id: int) -> int | None:
    """If any *other* `done` detail_url node for the same page exists, the page has
    already been successfully extracted and the current node is a duplicate that
    need not re-invoke the LLM. Matching is scheme-insensitive: `http://X` and
    `https://X` are treated as the same page (same-site links appear under both)."""
    stmt = (
        select(GraphNode.id)
        .where(
            GraphNode.type == NodeType.detail_url,
            GraphNode.url.in_(_scheme_variants(url)),
            GraphNode.status == NodeStatus.done,
            GraphNode.id != exclude_node_id,
        )
        .order_by(GraphNode.id.asc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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


async def _start_run(session, mode, settings, backup_path) -> int:
    run = CrawlRun(
        mode=mode,
        started_at=utcnow_iso(),
        status="running",
        settings_json=settings,
        backup_path=str(backup_path) if backup_path is not None else None,
    )
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


async def _update_org_unit_status(session, org_unit_id, status) -> None:
    row = (await session.execute(select(OrgUnit).where(OrgUnit.id == org_unit_id))).scalar_one_or_none()
    if row is not None:
        row.status = status
    await session.flush()


async def _update_university_status(session, status) -> None:
    meta = (await session.execute(select(UniversityMeta))).scalars().first()
    if meta is not None:
        meta.crawl_status = status
    await session.flush()


# --- reset helpers (SP7 --reset) ---
# Entry-point node types kept (reset to pending) when rebuilding an org_unit's
# subtree; everything else discovered below them is deleted so it can be
# re-discovered fresh with the fixed capture pipeline.
_RESET_ENTRYPOINT_TYPES = {NodeType.org_unit, NodeType.faculty_list_url}
_RESET_DELETABLE_TYPES = {
    NodeType.detail_url,
    NodeType.faculty_followup_url,
    NodeType.pagination_url,
}


async def _reset_org_unit_subtree(session, org_unit_id) -> dict:
    # 1. collect deletable nodes (the discovered subtree) and entry-point nodes.
    deletable_rows = (
        await session.execute(
            select(GraphNode.id, GraphNode.url).where(
                GraphNode.org_unit_id == org_unit_id,
                GraphNode.type.in_([t.value for t in _RESET_DELETABLE_TYPES]),
            )
        )
    ).all()
    entrypoint_rows = (
        await session.execute(
            select(GraphNode.id, GraphNode.url).where(
                GraphNode.org_unit_id == org_unit_id,
                GraphNode.type.in_([t.value for t in _RESET_ENTRYPOINT_TYPES]),
            )
        )
    ).all()

    deletable_ids = [r[0] for r in deletable_rows]
    deletable_urls = [r[1] for r in deletable_rows if r[1]]
    entrypoint_ids = [r[0] for r in entrypoint_rows]
    entrypoint_urls = [r[1] for r in entrypoint_rows if r[1]]

    edges_deleted = 0
    if deletable_ids:
        # 2. delete edges touching the deletable nodes.
        edges_deleted = (
            await session.execute(
                delete(GraphEdge).where(
                    or_(
                        GraphEdge.from_node_id.in_(deletable_ids),
                        GraphEdge.to_node_id.in_(deletable_ids),
                    )
                )
            )
        ).rowcount
        # 3. delete extraction attempts for the deletable nodes.
        await session.execute(
            delete(ExtractionAttempt).where(ExtractionAttempt.graph_node_id.in_(deletable_ids))
        )
        # 4. delete the deletable nodes.
        await session.execute(delete(GraphNode).where(GraphNode.id.in_(deletable_ids)))

    # 5. delete page_cache for both deleted and reset-to-pending entry-point URLs
    #    so they are re-fetched fresh.
    cache_urls = deletable_urls + entrypoint_urls
    caches_deleted = 0
    if cache_urls:
        caches_deleted = (
            await session.execute(delete(PageCache).where(PageCache.url.in_(cache_urls)))
        ).rowcount

    # 6. reset entry-point nodes back to pending so the engine re-crawls them.
    if entrypoint_ids:
        await session.execute(
            update(GraphNode)
            .where(GraphNode.id.in_(entrypoint_ids))
            .values(
                status=NodeStatus.pending,
                attempt_count=0,
                last_error=None,
                content_hash=None,
                claimed_at=None,
                completed_at=None,
                next_retry_at=None,
            )
        )

    # 7. reset the org_unit row's crawl status.
    await session.execute(
        update(OrgUnit).where(OrgUnit.id == org_unit_id).values(status="pending")
    )
    await session.flush()
    return {
        "org_unit_id": org_unit_id,
        "nodes_deleted": len(deletable_ids),
        "entrypoints_reset": len(entrypoint_ids),
        "edges_deleted": int(edges_deleted or 0),
        "caches_deleted": int(caches_deleted or 0),
    }


async def _reset_bad_detail_snapshots(session) -> dict:
    # detail_url leaf nodes whose cached snapshot is empty/whitespace or was
    # flagged terminal_unavailable:empty_page — the capture regression footprint.
    # SQLite's trim() only strips spaces, so pass an explicit whitespace charset
    # to also catch tab/newline-only snapshots.
    _ws = "\t\n\r "
    trimmed = func.trim(PageCache.text_snapshot, _ws)
    rows = (
        await session.execute(
            select(GraphNode.id, PageCache.url)
            .join(PageCache, PageCache.url == GraphNode.url)
            .where(
                GraphNode.type == NodeType.detail_url,
                or_(
                    trimmed.is_(None),
                    trimmed == "",
                    PageCache.block_reason.like("terminal_unavailable:empty_page%"),
                ),
            )
        )
    ).all()
    if not rows:
        return {"nodes_reset": 0, "caches_deleted": 0}

    node_ids = [r[0] for r in rows]
    cache_urls = [r[1] for r in rows if r[1]]

    await session.execute(
        update(GraphNode)
        .where(GraphNode.id.in_(node_ids))
        .values(
            status=NodeStatus.pending,
            attempt_count=0,
            last_error=None,
            content_hash=None,
            claimed_at=None,
            completed_at=None,
            next_retry_at=None,
        )
    )
    caches_deleted = 0
    if cache_urls:
        caches_deleted = (
            await session.execute(delete(PageCache).where(PageCache.url.in_(cache_urls)))
        ).rowcount
    await session.flush()
    return {
        "nodes_reset": len(node_ids),
        "caches_deleted": int(caches_deleted or 0),
    }
