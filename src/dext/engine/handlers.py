"""Graph node handlers for SP6."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlsplit

from dext.bridge.decision import PendingDecision
from dext.exclusions import is_valid_exclusion_reason
from dext.engine.names import clean_org_unit_name
from dext.engine.seeds import (
    _apply_probe_defer,
    _probe_skip_reason,
    node_spec,
    org_node_spec,
    resolve_discovered_url,
    resolve_discovered_urls,
)
from dext.engine.workers import ExtractTask
from dext.llm import DeciderContext, DeciderNode, decide_links
from dext.page import (
    FilterContext,
    PageSnapshot,
    extract_form_pagination_states,
    filter_detail_candidates,
    filter_navigation_candidates,
    find_url_pagination,
    merge_pagination_states,
    normalize_url,
)
from dext.storage.models import EdgeType, NodeStatus, NodeType
from dext.storage.writer import ClaimedNode, OrgUnitSpec
from dext.types import FetchAction, PaginationState

logger = logging.getLogger(__name__)

RESLICE_AXIS_PRIORITY = ("letter", "query:jxx", "query:yjjg", "query:jobType", "title", "advisor")
_QUERY_FILTER_IGNORED_PARAMS = {
    "keyword", "keywords", "search", "q", "kw", "key",
    "page", "p", "pn", "pageno", "pagenum", "curpage", "currentpage",
    "pageindex", "pagesize", "rows", "per_page",
    "__ycl_kind", "__ycl_form", "__ycl_field", "__ycl_page",
}
_ALL_FILTER_LABELS = ("全部", "不限", "所有")


@dataclass(frozen=True)
class _ResliceLink:
    url: str
    confidence: float | None
    facet_axis: str | None
    facet_value: str | None = None
    facet_is_all: bool = False
    facet_kind: str | None = None
    facet_reset_count: int = 0


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
        decision_center=None,
        redirect_guard=None,
        status_probe=None,
    ) -> None:
        self.storage = storage
        self.llm_client = llm_client
        self.settings = settings
        self.run_id = run_id
        self.university_name = university_name
        self.extract_queue = extract_queue
        self.reported_pagination_states = reported_pagination_states or []
        self.raw_html = raw_html
        self.decision_center = decision_center
        self.redirect_guard = redirect_guard
        self.status_probe = status_probe


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


def _merge_metadata(*parts: dict | None) -> dict:
    merged: dict = {}
    for part in parts:
        if not part:
            continue
        for key, value in part.items():
            merged.setdefault(key, value)
    return merged


def _child_depth(parent: ClaimedNode) -> int:
    return parent.depth + 1


def _within_depth(parent: ClaimedNode, settings) -> bool:
    return _child_depth(parent) <= settings.max_depth


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


def _link_exclusion_reason(link) -> str | None:
    reason = getattr(link, "exclusion_reason", None)
    return reason if is_valid_exclusion_reason(reason) else None


def _page_exclusion_reason(decision) -> str | None:
    reason = getattr(decision, "page_exclusion_reason", None)
    return reason if is_valid_exclusion_reason(reason) else None


def _query_pairs(url: str) -> dict[str, list[str]]:
    return {
        key: values
        for key, values in _query_pairs_all(url).items()
        if key.lower() not in _QUERY_FILTER_IGNORED_PARAMS
    }


def _query_pairs_all(url: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        out.setdefault(key, []).append(value)
    return out


def _query_value(values: list[str] | None) -> str:
    return values[-1] if values else ""


def _is_all_filter_label(label: str | None) -> bool:
    text = (label or "").strip()
    return bool(text) and any(token in text for token in _ALL_FILTER_LABELS)


def _same_query_filter_surface(current_url: str, candidate_url: str) -> bool:
    current = urlsplit(current_url)
    candidate = urlsplit(candidate_url)
    current_host = (current.hostname or "").lower()
    candidate_host = (candidate.hostname or "").lower()
    return (
        current.scheme.lower() == candidate.scheme.lower()
        and current_host == candidate_host
        and current.path.rstrip("/") == candidate.path.rstrip("/")
        and current.query != candidate.query
    )


def _query_filter_changes(current_url: str, candidate_url: str) -> dict[str, tuple[str, str]]:
    current = _query_pairs(current_url)
    candidate = _query_pairs(candidate_url)
    keys = set(current) | set(candidate)
    changes: dict[str, tuple[str, str]] = {}
    for key in keys:
        before = _query_value(current.get(key))
        after = _query_value(candidate.get(key))
        if before != after:
            changes[key] = (before, after)
    return changes


def _query_axis_name(param: str) -> str:
    return f"query:{param}"


def _infer_query_filter_reslice(current_url: str, link, signal) -> _ResliceLink | None:
    if getattr(link, "label", None) in {"detail", "pagination", "login", "noise"}:
        return None
    if getattr(link, "is_leaf", False):
        return None
    if not _same_query_filter_surface(current_url, getattr(link, "url", "")):
        return None
    changes = _query_filter_changes(current_url, link.url)
    if not changes:
        return None
    reset_changes = [(key, before, after) for key, (before, after) in changes.items() if before and not after]
    if reset_changes:
        key, _before, after = _best_query_change(reset_changes)
        axis = _query_axis_name(key)
        return _ResliceLink(
            url=link.url,
            confidence=getattr(link, "confidence", None),
            facet_axis=axis,
            facet_value=after,
            facet_is_all=True,
            facet_kind="query_filter",
            facet_reset_count=len(reset_changes),
        )
    nonempty_changes = [(key, before, after) for key, (before, after) in changes.items() if after]
    if not nonempty_changes:
        label = getattr(signal, "anchor_text", None)
        key, _before, after = _best_query_change([(key, before, after) for key, (before, after) in changes.items()])
        return _ResliceLink(
            url=link.url,
            confidence=getattr(link, "confidence", None),
            facet_axis=_query_axis_name(key),
            facet_value=after,
            facet_is_all=_is_all_filter_label(label),
            facet_kind="query_filter",
            facet_reset_count=0,
        )
    key, _before, after = _best_query_change(nonempty_changes)
    return _ResliceLink(
        url=link.url,
        confidence=getattr(link, "confidence", None),
        facet_axis=_query_axis_name(key),
        facet_value=after,
        facet_is_all=False,
        facet_kind="query_filter",
        facet_reset_count=0,
    )


def _best_query_change(changes: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    return sorted(changes, key=lambda item: (_reslice_axis_rank(_query_axis_name(item[0])), item[0]))[0]


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
    college_links = []
    for link in decision.links:
        if _link_exclusion_reason(link):
            continue
        if link.label != "college":
            continue
        name = clean_org_unit_name(link.org_unit_name)
        if not name:
            continue
        college_links.append((link, name))
    resolved = await resolve_discovered_urls(
        [link.url for link, _ in college_links],
        redirect_guard=deps.redirect_guard,
        status_probe=deps.status_probe,
    )
    for (link, name), (resolved_url, redirect_metadata) in zip(college_links, resolved):
        college_probe_skip = _probe_skip_reason(redirect_metadata)
        if resolved_url is None and college_probe_skip is None:
            continue
        college_url = resolved_url or link.url
        org_id = await deps.storage.writer.upsert_org_unit(
            OrgUnitSpec(name=name, url=college_url, kind="college", discovered_from_url=snapshot.url)
        )
        node_status = NodeStatus.pending
        node_metadata = _merge_metadata(
            {"discovered_from_url": snapshot.url, "identity_url": college_url, "source_url": link.url},
            redirect_metadata,
        )
        if college_probe_skip:
            node_status = NodeStatus.skipped
            node_metadata = _merge_metadata(
                node_metadata, {"reason": college_probe_skip, "probe_skipped": True}
            )
        org_node_id = await deps.storage.writer.upsert_node(
            org_node_spec(
                org_unit_id=org_id,
                org_unit_name=name,
                url=college_url,
                settings=deps.settings,
                run_id=deps.run_id,
                depth=_child_depth(node),
                metadata=node_metadata,
                status=node_status,
            )
        )
        await _apply_probe_defer(deps.storage, org_node_id, node_metadata)
        await deps.storage.writer.add_edge(node.id, org_node_id, EdgeType.discovered_on_page, confidence=link.confidence)
        created += 1
    await deps.storage.writer.mark_node(node.id, NodeStatus.done, content_hash=snapshot.content_hash)
    excluded = sum(1 for link in decision.links if getattr(link, "exclusion_reason", None))
    if excluded:
        logger.info("org listing %s decider-excluded %d links", snapshot.url, excluded)
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
    precomputed_resolved: tuple[str | None, dict] | None = None,
) -> int | None:
    if precomputed_resolved is not None:
        resolved_url, redirect_metadata = precomputed_resolved
    else:
        resolved_url, redirect_metadata = await resolve_discovered_url(
            url, redirect_guard=deps.redirect_guard, status_probe=deps.status_probe
        )
    probe_skip = _probe_skip_reason(redirect_metadata)
    if resolved_url is None and probe_skip is None:
        # BLOCKED redirect or allowlist-rejected host → drop (no node created).
        return None
    child_url = resolved_url or url
    canonical_identity_url = child_url if child_url != url else (identity_url or url)
    node_status = NodeStatus.pending
    node_metadata = _merge_metadata(
        metadata,
        {"identity_url": canonical_identity_url, "discovered_from_url": url},
        redirect_metadata,
    )
    if probe_skip:
        # 入图前 status probe 判定为死链(404/410):建 skipped 节点保留图完整性 +
        # 可诊断;skipped 不被 claim_next 取,绝不交给前端 fetch。
        node_status = NodeStatus.skipped
        node_metadata = _merge_metadata(node_metadata, {"reason": probe_skip, "probe_skipped": True})
    spec = node_spec(
        node_type,
        url=child_url,
        settings=deps.settings,
        run_id=deps.run_id,
        org_unit_id=parent.org_unit_id,
        org_unit_name=parent.org_unit_name,
        depth=_child_depth(parent),
        metadata=node_metadata,
        confidence=confidence,
        node_key_url=canonical_identity_url,
        subtree=parent.org_unit_id is not None,
        status=node_status,
    )
    child_id = await deps.storage.writer.upsert_node(spec)
    # 入图前探到 5xx/probe_failed → 延迟到 next_retry_at,本 run claim_next 跳过,
    # 单线程浏览器抓取器绝不碰会阻塞的坏网关/redirect-loop URL。
    await _apply_probe_defer(deps.storage, child_id, node_metadata)
    await deps.storage.writer.add_edge(parent.id, child_id, edge_type, confidence=confidence, metadata=node_metadata)
    return child_id


async def handle_org_unit(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    if node.org_unit_id is not None:
        await deps.storage.writer.update_org_unit_status(node.org_unit_id, "in_progress")
    decision = await _decide(snapshot, snapshot.link_signals, node, deps)
    page_exclusion_reason = _page_exclusion_reason(decision)
    if page_exclusion_reason:
        if node.org_unit_id is not None:
            await deps.storage.writer.update_org_unit_status(node.org_unit_id, "no_faculty_page")
        await deps.storage.writer.mark_node(
            node.id,
            NodeStatus.skipped,
            last_error=f"excluded:{page_exclusion_reason}",
            content_hash=snapshot.content_hash,
        )
        logger.info("skipped excluded org unit %s reason=%s", snapshot.url, page_exclusion_reason)
        return
    created = 0
    if _within_depth(node, deps.settings):
        for link in decision.links:
            if _link_exclusion_reason(link):
                continue
            if link.label == "faculty_list":
                child_id = await _create_child(
                    deps,
                    node,
                    NodeType.faculty_list_url,
                    url=link.url,
                    edge_type=EdgeType.belongs_to_org_unit,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                if child_id is not None:
                    created += 1
            elif link.label == "followup":
                child_id = await _create_child(
                    deps,
                    node,
                    NodeType.faculty_followup_url,
                    url=link.url,
                    edge_type=EdgeType.discovered_on_page,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                if child_id is not None:
                    created += 1
            elif link.label == "detail":
                child_id = await _create_child(
                    deps,
                    node,
                    NodeType.detail_url,
                    url=link.url,
                    edge_type=EdgeType.detail_candidate_of,
                    confidence=link.confidence,
                    metadata={"label": link.label},
                )
                if child_id is not None:
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
        child_id = await _create_child(
            deps,
            node,
            NodeType.pagination_url,
            url=cand.url,
            edge_type=EdgeType.pagination_of,
            metadata={"label": cand.label, "page_index": cand.page_index, "pagination_kind": "url"},
        )
        if child_id is not None:
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
        child_id = await _create_child(
            deps,
            node,
            NodeType.pagination_url,
            url=state.url,
            edge_type=EdgeType.pagination_of,
            metadata=metadata,
            identity_url=state.synthetic_url,
        )
        if child_id is not None:
            count += 1
    return count


async def _materialize_decided(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    decision,
    filter_result,
    *,
    pagination_created: int = 0,
    over_budget: bool = False,
) -> int:
    count = 0
    if not _within_depth(node, deps.settings):
        return 0
    actionable = [link for link in decision.links if not _link_exclusion_reason(link)]
    signal_by_url = {sig.url: sig for sig in filter_result.kept}
    query_reslices: list[_ResliceLink] = []
    regular_links = []
    for link in actionable:
        inferred = _infer_query_filter_reslice(snapshot.url, link, signal_by_url.get(link.url))
        if inferred is not None:
            query_reslices.append(inferred)
        else:
            regular_links.append(link)
    has_detail = any(link.label == "detail" or link.is_leaf for link in regular_links if link.label != "reslice")
    has_followup = any(link.label == "followup" for link in regular_links)
    yields_people = has_detail or has_followup or pagination_created > 0
    reslice_links = [link for link in regular_links if link.label == "reslice"]
    reslice_links.extend(query_reslices)

    materialize_links = [link for link in regular_links if link.label != "reslice"]
    precomputed = await resolve_discovered_urls(
        [link.url for link in materialize_links],
        redirect_guard=deps.redirect_guard,
        status_probe=deps.status_probe,
    )
    resolved_by_url: dict[str, tuple[str | None, dict]] = {}
    for link, result in zip(materialize_links, precomputed):
        resolved_by_url[link.url] = result

    for link in materialize_links:
        if link.label == "detail" or link.is_leaf:
            child_id = await _create_child(
                deps, node, NodeType.detail_url, url=link.url,
                edge_type=EdgeType.detail_candidate_of, confidence=link.confidence,
                metadata={"label": link.label},
                precomputed_resolved=resolved_by_url.get(link.url),
            )
            if child_id is not None:
                count += 1
        elif link.label == "pagination":
            if over_budget:
                continue
            child_id = await _create_child(
                deps, node, NodeType.pagination_url, url=link.url,
                edge_type=EdgeType.pagination_of, confidence=link.confidence,
                metadata={"label": link.label, "pagination_kind": "decider"},
                precomputed_resolved=resolved_by_url.get(link.url),
            )
            if child_id is not None:
                count += 1
        elif link.label == "followup":
            if over_budget:
                continue
            child_id = await _create_child(
                deps, node, NodeType.faculty_followup_url, url=link.url,
                edge_type=EdgeType.discovered_on_page, confidence=link.confidence,
                metadata={"label": link.label},
                precomputed_resolved=resolved_by_url.get(link.url),
            )
            if child_id is not None:
                count += 1
    count += await _materialize_reslices(
        node, snapshot, deps, reslice_links, yields_people=yields_people, over_budget=over_budget
    )
    logger.info(
        "navigation filter for %s kept=%d dropped=%s selected=%d parse_error=%s",
        snapshot.url, len(filter_result.kept), filter_result.dropped, count, decision.parse_error,
    )
    if decision.parse_error:
        raise RuntimeError("decider_invalid_json")
    if filter_result.kept and count == 0 and not decision.links:
        raise RuntimeError("decider_no_navigation_links")
    return count


async def _materialize_reslices(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    reslice_links: list,
    *,
    yields_people: bool,
    over_budget: bool = False,
) -> int:
    if not reslice_links:
        return 0
    if over_budget:
        for axis, n in Counter(_facet_axis(link) for link in reslice_links).items():
            logger.info("redundant_facet:%s dropped=%d url=%s", axis, n, snapshot.url)
        return 0
    all_links = [link for link in reslice_links if getattr(link, "facet_is_all", False)]
    if all_links:
        chosen_link = _choose_all_reslice(all_links)
        chosen = _facet_axis(chosen_link)
        child_id = await _create_child(
            deps, node, NodeType.faculty_followup_url, url=chosen_link.url,
            edge_type=EdgeType.discovered_on_page, confidence=getattr(chosen_link, "confidence", None),
            metadata=_reslice_metadata(chosen_link, chosen),
        )
        if child_id is None:
            return 0
        dropped = [link for link in reslice_links if link is not chosen_link]
        for axis, n in Counter(_facet_axis(link) for link in dropped).items():
            logger.info("redundant_facet:%s dropped=%d url=%s", axis, n, snapshot.url)
        return 1
    if yields_people:
        # Trust-broad: the same people are already reachable here → collapse all re-slices.
        for axis, n in Counter(_facet_axis(link) for link in reslice_links).items():
            logger.info("redundant_facet:%s dropped=%d url=%s", axis, n, snapshot.url)
        return 0
    # No people anywhere on this page → re-slices are the only way forward; descend ONE axis.
    chosen = _choose_reslice_axis(reslice_links)
    count = 0
    for link in reslice_links:
        if _facet_axis(link) != chosen:
            continue
        child_id = await _create_child(
            deps, node, NodeType.faculty_followup_url, url=link.url,
            edge_type=EdgeType.discovered_on_page, confidence=getattr(link, "confidence", None),
            metadata=_reslice_metadata(link, chosen),
        )
        if child_id is not None:
            count += 1
    for axis in {_facet_axis(link) for link in reslice_links} - {chosen}:
        logger.info("reslice_axis_skipped:%s url=%s", axis, snapshot.url)
    return count


def _facet_axis(link) -> str:
    return getattr(link, "facet_axis", None) or "unknown"


def _reslice_metadata(link, axis: str) -> dict:
    metadata = {"label": "reslice", "reslice": True, "facet_axis": axis}
    if getattr(link, "facet_kind", None):
        metadata["facet_kind"] = link.facet_kind
    if hasattr(link, "facet_value"):
        metadata["facet_value"] = getattr(link, "facet_value", None)
    if hasattr(link, "facet_is_all"):
        metadata["facet_is_all"] = bool(getattr(link, "facet_is_all", False))
    return metadata


def _choose_all_reslice(reslice_links: list) -> object:
    return sorted(
        reslice_links,
        key=lambda link: (
            -int(getattr(link, "facet_reset_count", 0) or 0),
            _reslice_axis_rank(_facet_axis(link)),
            getattr(link, "url", ""),
        ),
    )[0]


def _choose_reslice_axis(reslice_links: list) -> str:
    present = {_facet_axis(link) for link in reslice_links}
    return sorted(present, key=lambda axis: (_reslice_axis_rank(axis), axis))[0]


def _reslice_axis_rank(axis: str) -> int:
    try:
        return RESLICE_AXIS_PRIORITY.index(axis)
    except ValueError:
        return len(RESLICE_AXIS_PRIORITY)


async def _over_facet_budget(node: ClaimedNode, deps: HandlerDeps) -> bool:
    if node.org_unit_id is None:
        return False
    budget = getattr(deps.settings, "facet_node_budget", 0) or 0
    if budget <= 0:
        return False
    count = await deps.storage.writer.count_subtree_facet_nodes(node.org_unit_id)
    if count < budget:
        return False
    logger.info("facet_budget_exceeded org_unit=%s budget=%d count=%d", node.org_unit_id, budget, count)
    _maybe_set_budget_decision(node, deps, count)
    return True


def _maybe_set_budget_decision(node: ClaimedNode, deps: HandlerDeps, count: int) -> None:
    center = getattr(deps, "decision_center", None)
    if center is None or center.current() is not None:
        return
    center.set_decision(
        PendingDecision(
            id=f"facet_budget:{node.org_unit_id}",
            kind="facet_budget",
            org_unit_name=node.org_unit_name or "",
            failure_count=count,
            sample_urls=[],
            suggested_action="stop_subtree",
        )
    )


async def handle_faculty_page(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    filter_result = filter_navigation_candidates(
        snapshot,
        FilterContext(faculty_list_url=snapshot.url, already_enriched=set()),
    )
    decision = await _decide(snapshot, filter_result.kept, node, deps)
    page_exclusion_reason = _page_exclusion_reason(decision)
    if page_exclusion_reason:
        await deps.storage.writer.mark_node(
            node.id,
            NodeStatus.skipped,
            last_error=f"excluded:{page_exclusion_reason}",
            content_hash=snapshot.content_hash,
        )
        logger.info("skipped excluded faculty page %s reason=%s", snapshot.url, page_exclusion_reason)
        return
    over_budget = await _over_facet_budget(node, deps)
    created = 0
    if not over_budget:
        detail_like_urls = _detail_like_urls(snapshot)
        created += await _create_url_pagination_nodes(node, snapshot, deps, exclude_urls=detail_like_urls)
        created += await _create_form_pagination_nodes(node, snapshot, deps)
    try:
        created += await _materialize_decided(
            node, snapshot, deps, decision, filter_result,
            pagination_created=created, over_budget=over_budget,
        )
    except RuntimeError as exc:
        reason = str(exc) or "decider_failed"
        await deps.storage.writer.mark_node(
            node.id,
            NodeStatus.retry,
            last_error=reason,
            content_hash=snapshot.content_hash,
        )
        logger.info("retry faculty page %s reason=%s", snapshot.url, reason)
        return
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
    normalized = normalize_url(url, base)
    if normalized is not None:
        return normalized
    parts = urlsplit(url.strip())
    return url.strip() if parts.scheme and parts.scheme not in ("http", "https") else ""
