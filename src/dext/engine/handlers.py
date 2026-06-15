"""Graph node handlers for SP6."""

from __future__ import annotations

import logging
from dataclasses import asdict

from dext.engine.names import clean_org_unit_name
from dext.engine.seeds import node_spec, org_node_spec
from dext.engine.workers import ExtractTask
from dext.llm import DeciderContext, DeciderNode, decide_links
from dext.page import (
    FilterContext,
    PageSnapshot,
    extract_form_pagination_states,
    filter_detail_candidates,
    find_followup_links,
    find_url_pagination,
    merge_pagination_states,
    normalize_url,
)
from dext.storage.models import EdgeType, NodeStatus, NodeType
from dext.storage.writer import ClaimedNode, OrgUnitSpec
from dext.types import FetchAction, PaginationState

logger = logging.getLogger(__name__)

_NON_TEACHING_UNIT_TOKENS = (
    "体育",
    "艺术",
    "继续教育",
    "国际教育",
    "招生",
    "就业",
    "图书馆",
    "校友",
    "后勤",
    "附属",
    "机关",
    "行政",
)


class HandlerDeps:
    def __init__(
        self,
        *,
        storage,
        llm_client,
        settings,
        run_id: int,
        university_name: str,
        extract_queue,
        reported_pagination_states: list[PaginationState] | None = None,
        raw_html: str = "",
    ) -> None:
        self.storage = storage
        self.llm_client = llm_client
        self.settings = settings
        self.run_id = run_id
        self.university_name = university_name
        self.extract_queue = extract_queue
        self.reported_pagination_states = reported_pagination_states or []
        self.raw_html = raw_html


def fetch_action_from_metadata(metadata: dict | None) -> FetchAction | None:
    data = (metadata or {}).get("fetch_action")
    if not isinstance(data, dict):
        return None
    return FetchAction(
        kind=data.get("kind", "form_submit"),
        form_name=data.get("form_name"),
        fields=data.get("fields"),
        submit=data.get("submit"),
        synthetic_url=data.get("synthetic_url"),
        label=data.get("label"),
        page_index=data.get("page_index"),
        state_id=data.get("state_id"),
    )


def identity_url_for(node: ClaimedNode) -> str:
    metadata = node.metadata or {}
    return metadata.get("identity_url") or node.url


def _child_depth(parent: ClaimedNode) -> int:
    return parent.depth + 1


def _within_depth(parent: ClaimedNode, settings) -> bool:
    return _child_depth(parent) <= settings.max_depth


def _is_non_teaching_unit(name: str) -> bool:
    return any(token in name for token in _NON_TEACHING_UNIT_TOKENS)


def _signal_by_url(snapshot: PageSnapshot, url: str):
    return next((s for s in snapshot.link_signals if s.url == url), None)


def _looks_like_pager_label(label: str | None) -> bool:
    text = (label or "").strip().lower()
    if not text:
        return False
    if text.isdigit():
        return True
    return text in {"首页", "末页", "尾页", "上一页", "下一页", "prev", "next", "first", "last"}


def _detail_like_urls(snapshot: PageSnapshot) -> set[str]:
    filtered = filter_detail_candidates(
        snapshot,
        FilterContext(faculty_list_url=snapshot.url, already_enriched=set()),
    )
    return {sig.url for sig in filtered.kept if not _looks_like_pager_label(sig.anchor_text)}


async def dispatch(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    node_type = NodeType(node.type)
    if node_type == NodeType.org_listing_url:
        await handle_org_listing(node, snapshot, deps)
    elif node_type == NodeType.org_unit:
        await handle_org_unit(node, snapshot, deps)
    elif node_type in {NodeType.faculty_list_url, NodeType.pagination_url, NodeType.faculty_followup_url}:
        await handle_faculty_page(node, snapshot, deps)
    elif node_type == NodeType.detail_url:
        await handle_detail(node, snapshot, deps)
    else:
        await deps.storage.writer.mark_node(node.id, NodeStatus.failed, last_error=f"unknown_node_type:{node.type}")


async def _decide(snapshot: PageSnapshot, candidates, node: ClaimedNode, deps: HandlerDeps):
    return await decide_links(
        snapshot,
        candidates,
        DeciderNode(type=node.type, url=snapshot.url, depth=node.depth, org_unit_name=node.org_unit_name),
        DeciderContext(
            university_name=deps.university_name,
            faculty_list_url=snapshot.url if node.type == NodeType.faculty_list_url else "",
        ),
        client=deps.llm_client,
    )


async def handle_org_listing(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    decision = await _decide(snapshot, snapshot.link_signals, node, deps)
    created = 0
    for link in decision.links:
        if link.label != "college":
            continue
        name = clean_org_unit_name(link.org_unit_name)
        if not name or _is_non_teaching_unit(name):
            continue
        org_id = await deps.storage.writer.upsert_org_unit(
            OrgUnitSpec(name=name, url=link.url, kind="college", discovered_from_url=snapshot.url)
        )
        org_node_id = await deps.storage.writer.upsert_node(
            org_node_spec(
                org_unit_id=org_id,
                org_unit_name=name,
                url=link.url,
                settings=deps.settings,
                run_id=deps.run_id,
                depth=_child_depth(node),
                metadata={"discovered_from": snapshot.url},
            )
        )
        await deps.storage.writer.add_edge(node.id, org_node_id, EdgeType.discovered_on_page, confidence=link.confidence)
        created += 1
    await deps.storage.writer.mark_node(node.id, NodeStatus.done, content_hash=snapshot.content_hash)
    logger.info("org listing %s created %d org units", snapshot.url, created)


async def _create_child(
    deps: HandlerDeps,
    parent: ClaimedNode,
    node_type: NodeType,
    *,
    url: str,
    edge_type: EdgeType,
    confidence: float | None = None,
    metadata: dict | None = None,
    identity_url: str | None = None,
) -> int:
    spec = node_spec(
        node_type,
        url=url,
        settings=deps.settings,
        run_id=deps.run_id,
        org_unit_id=parent.org_unit_id,
        org_unit_name=parent.org_unit_name,
        depth=_child_depth(parent),
        metadata=metadata,
        confidence=confidence,
        node_key_url=identity_url,
        subtree=parent.org_unit_id is not None,
    )
    child_id = await deps.storage.writer.upsert_node(spec)
    await deps.storage.writer.add_edge(parent.id, child_id, edge_type, confidence=confidence, metadata=metadata)
    return child_id


async def handle_org_unit(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    if node.org_unit_id is not None:
        await deps.storage.writer.update_org_unit_status(node.org_unit_id, "in_progress")
    decision = await _decide(snapshot, snapshot.link_signals, node, deps)
    created = 0
    if _within_depth(node, deps.settings):
        for link in decision.links:
            if link.label == "faculty_list":
                await _create_child(
                    deps,
                    node,
                    NodeType.faculty_list_url,
                    url=link.url,
                    edge_type=EdgeType.belongs_to_org_unit,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                created += 1
            elif link.label == "followup":
                await _create_child(
                    deps,
                    node,
                    NodeType.faculty_followup_url,
                    url=link.url,
                    edge_type=EdgeType.discovered_on_page,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                created += 1
            elif link.label == "detail":
                await _create_child(
                    deps,
                    node,
                    NodeType.detail_url,
                    url=link.url,
                    edge_type=EdgeType.detail_candidate_of,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                created += 1
    if created:
        if node.org_unit_id is not None:
            await deps.storage.writer.update_org_unit_status(node.org_unit_id, "completed")
        await deps.storage.writer.mark_node(node.id, NodeStatus.done, content_hash=snapshot.content_hash)
    else:
        if node.org_unit_id is not None:
            await deps.storage.writer.update_org_unit_status(node.org_unit_id, "no_faculty_page")
        await deps.storage.writer.mark_node(node.id, NodeStatus.skipped, last_error="no_faculty_page")


async def _create_url_pagination_nodes(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    *,
    exclude_urls: set[str] | None = None,
) -> int:
    if not _within_depth(node, deps.settings):
        return 0
    count = 0
    excluded = exclude_urls or set()
    for cand in find_url_pagination(snapshot, snapshot.url):
        if cand.url in excluded:
            continue
        await _create_child(
            deps,
            node,
            NodeType.pagination_url,
            url=cand.url,
            edge_type=EdgeType.pagination_of,
            metadata={"label": cand.label, "page_index": cand.page_index, "pagination_kind": "url"},
        )
        count += 1
    return count


async def _create_followup_nodes(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    *,
    exclude_urls: set[str] | None = None,
) -> int:
    if not _within_depth(node, deps.settings):
        return 0
    count = 0
    excluded = exclude_urls or set()
    for cand in find_followup_links(snapshot, snapshot.url, limit=deps.settings.followup_page_limit):
        if cand.url in excluded:
            continue
        await _create_child(
            deps,
            node,
            NodeType.faculty_followup_url,
            url=cand.url,
            edge_type=EdgeType.discovered_on_page,
            metadata={"label": cand.label, "followup_kind": "category"},
        )
        count += 1
    return count


def _action_from_state(state: PaginationState) -> FetchAction:
    return FetchAction(
        kind=state.kind,
        form_name=state.form_name,
        fields=state.fields,
        submit=state.submit,
        synthetic_url=state.synthetic_url,
        label=state.label,
        page_index=state.page_index,
        state_id=state.state_id,
    )


async def _create_form_pagination_nodes(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> int:
    if not _within_depth(node, deps.settings):
        return 0
    parsed = extract_form_pagination_states(deps.raw_html, snapshot.url)
    states = merge_pagination_states(deps.reported_pagination_states, parsed)
    count = 0
    for state in states:
        action = _action_from_state(state)
        metadata = {
            "identity_url": state.synthetic_url,
            "fetch_action": asdict(action),
            "pagination_kind": "form",
            "page_index": state.page_index,
            "state_id": state.state_id,
        }
        await _create_child(
            deps,
            node,
            NodeType.pagination_url,
            url=state.url,
            edge_type=EdgeType.pagination_of,
            metadata=metadata,
            identity_url=state.synthetic_url,
        )
        count += 1
    return count


async def _create_decided_nodes(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> int:
    filter_result = filter_detail_candidates(
        snapshot,
        FilterContext(faculty_list_url=snapshot.url, already_enriched=set()),
    )
    decision = await _decide(snapshot, filter_result.kept, node, deps)
    count = 0
    if not _within_depth(node, deps.settings):
        return 0
    for link in decision.links:
        if link.label == "detail" or link.is_leaf:
            await _create_child(
                deps,
                node,
                NodeType.detail_url,
                url=link.url,
                edge_type=EdgeType.detail_candidate_of,
                confidence=link.confidence,
                metadata={"label": link.label},
            )
            count += 1
        elif link.label == "pagination":
            await _create_child(
                deps,
                node,
                NodeType.pagination_url,
                url=link.url,
                edge_type=EdgeType.pagination_of,
                confidence=link.confidence,
                metadata={"label": link.label, "pagination_kind": "decider"},
            )
            count += 1
        elif link.label == "followup":
            await _create_child(
                deps,
                node,
                NodeType.faculty_followup_url,
                url=link.url,
                edge_type=EdgeType.discovered_on_page,
                confidence=link.confidence,
                metadata={"label": link.label},
            )
            count += 1
    logger.info("detail filter for %s kept=%d dropped=%s", snapshot.url, len(filter_result.kept), filter_result.dropped)
    return count


async def handle_faculty_page(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    created = 0
    detail_like_urls = _detail_like_urls(snapshot)
    created += await _create_url_pagination_nodes(node, snapshot, deps, exclude_urls=detail_like_urls)
    created += await _create_followup_nodes(node, snapshot, deps, exclude_urls=detail_like_urls)
    created += await _create_form_pagination_nodes(node, snapshot, deps)
    created += await _create_decided_nodes(node, snapshot, deps)
    await deps.storage.writer.mark_node(node.id, NodeStatus.done, content_hash=snapshot.content_hash)
    logger.info("faculty page %s created %d child nodes", snapshot.url, created)


async def handle_detail(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    await deps.extract_queue.put(
        ExtractTask(
            node_id=node.id,
            node_key=node.node_key,
            snapshot=snapshot,
            org_unit_id=node.org_unit_id,
            org_unit_name=node.org_unit_name or "",
            attempt_count=node.attempt_count,
        )
    )


def normalize_discovered_url(url: str, base: str) -> str:
    return normalize_url(url, base) or url
