"""Seed loading and graph-node construction helpers for SP6."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from dext.bridge.redirect import BLOCKED, PROBE_FAILED, PROBE_TIMEOUT as REDIRECT_PROBE_TIMEOUT, RedirectGuard
from dext.bridge.probe import (
    DEAD_CAT,
    PROBE_FAILED as STATUS_PROBE_FAILED,
    PROBE_TIMEOUT as STATUS_PROBE_TIMEOUT,
    RATE_LIMITED_CAT,
    TRANSIENT_CAT,
    StatusProbe,
)
from dext.page.urls import normalize_url
from dext.seed import OrgUnitSeed, UniversitySeed
from dext.storage.dedup import node_key_for
from dext.storage.models import EdgeType, NodeStatus, NodeType
from dext.storage.writer import NodeSpec, OrgUnitSpec
from dext.url_policy import is_allowed_fetch_host
from dext.engine.priorities import BASE_PRIORITY_BY_TYPE, priority_for as base_priority_for, subtree_priority_for

PRIORITY_BY_TYPE: dict[NodeType, float] = BASE_PRIORITY_BY_TYPE

_PROBE_CONCURRENCY = 16
# 入图前 probe 探到 5xx / 不可达时,把节点延迟到多久之后才允许 driver claim。
# 目的:避免单线程浏览器抓取器卡在坏网关 URL 上阻塞整个 crawl。下次 --resume
# 会重新 probe,若网关恢复则正常 pending 入抓取。
PROBE_DEFER_HOURS = 24


@dataclass
class SeedLoadSummary:
    org_listing_nodes: int = 0
    org_units: int = 0
    org_unit_nodes: int = 0
    faculty_list_nodes: int = 0
    edges: int = 0


async def resolve_discovered_url(
    url: str,
    *,
    redirect_guard: RedirectGuard | None = None,
    status_probe: StatusProbe | None = None,
) -> tuple[str | None, dict[str, object]]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        final_url = url
        metadata: dict[str, object] = {"source_url": url}
    elif redirect_guard is None and status_probe is None:
        final_url = url
        metadata = {"source_url": url}
    else:
        final_url = url
        metadata = {"source_url": url}
        if redirect_guard is not None:
            verdict = await redirect_guard.probe_redirect(url)
            if verdict.verdict == BLOCKED:
                return None, {}
            if verdict.verdict == PROBE_FAILED:
                metadata["redirect_probe_failed"] = True
            elif verdict.verdict == REDIRECT_PROBE_TIMEOUT:
                # 探针超时 = 后端旁路够不到,不代表浏览器也够不到。不丢、不 defer,
                # 仅留诊断标记;URL 照常入图 pending → 本 run 可被 claim → fetch。
                metadata["redirect_probe_timeout"] = True
            else:
                final_url = verdict.final_url or url
                if final_url != url:
                    metadata["redirect_verdict"] = verdict.verdict
                    if verdict.reason:
                        metadata["redirect_reason"] = verdict.reason
        if status_probe is not None and parts.scheme in ("http", "https"):
            sverdict = await status_probe.probe(final_url)
            if sverdict.category == DEAD_CAT:
                return None, {"probe_skip_reason": sverdict.reason or "dead"}
            if sverdict.category == STATUS_PROBE_FAILED:
                # probe 侧不可达/redirect-loop → 也可能阻塞浏览器抓取 → 延迟重试
                metadata["probe_defer_reason"] = "probe_failed"
            elif sverdict.category == STATUS_PROBE_TIMEOUT:
                # 探针超时 = 无信号(后端裸 GET 慢/被 UA 拦截,真人浏览器常能打开)。
                # 不 defer、不 skip、不丢 → 节点照常 pending → 本 run 交前端 fetch。
                metadata["probe_timeout"] = True
            elif sverdict.category == TRANSIENT_CAT:
                # 5xx 网关故障:本 run 跳过浏览器抓取以免阻塞,延迟到 PROBE_DEFER_HOURS 后
                metadata["probe_defer_reason"] = sverdict.reason or "transient"
            elif sverdict.category == RATE_LIMITED_CAT:
                # 429 不阻塞浏览器(快速返回),保持 pending,由 driver RateThrottle 处理
                metadata["probe_status"] = sverdict.reason
    if not is_allowed_fetch_host(final_url):
        return None, {}
    return final_url, metadata


async def resolve_discovered_urls(
    urls: list[str],
    *,
    redirect_guard: RedirectGuard | None = None,
    status_probe: StatusProbe | None = None,
) -> list[tuple[str | None, dict[str, object]]]:
    """Batch parallel resolve for many discovered URLs.

    Probes run concurrently under a semaphore (caps per-host WAF pressure);
    upserts stay serial at the call site. ``None`` guards short-circuit to
    the identity resolution for every URL (still respects the fetch host
    allowlist).
    """
    if not urls:
        return []
    if redirect_guard is None and status_probe is None:
        return [
            await resolve_discovered_url(u, redirect_guard=None, status_probe=None) for u in urls
        ]

    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def _one(u: str) -> tuple[str | None, dict[str, object]]:
        async with sem:
            return await resolve_discovered_url(
                u, redirect_guard=redirect_guard, status_probe=status_probe
            )

    return await asyncio.gather(*(_one(u) for u in urls))


def node_spec(
    node_type: NodeType,
    *,
    url: str,
    settings,
    run_id: int | None = None,
    org_unit_id: int | None = None,
    org_unit_name: str | None = None,
    depth: int = 0,
    metadata: dict | None = None,
    status: NodeStatus = NodeStatus.pending,
    confidence: float | None = None,
    node_key_url: str | None = None,
    subtree: bool = False,
) -> NodeSpec:
    identity_url = node_key_url or url
    key = node_key_for(node_type, normalized_url=identity_url, org_unit_id=org_unit_id)
    priority = subtree_priority_for(node_type) if subtree else base_priority_for(node_type)
    return NodeSpec(
        node_key=key,
        type=node_type,
        url=url,
        org_unit_id=org_unit_id,
        org_unit_name=org_unit_name,
        status=status,
        priority_score=priority,
        base_priority=priority,
        confidence=confidence,
        depth=depth,
        max_attempts=settings.max_attempts,
        run_id=run_id,
        metadata=metadata,
    )


def org_node_spec(
    *,
    org_unit_id: int | None,
    org_unit_name: str,
    url: str,
    settings,
    run_id: int | None = None,
    status: NodeStatus = NodeStatus.pending,
    depth: int = 0,
    metadata: dict | None = None,
) -> NodeSpec:
    key = node_key_for(NodeType.org_unit, org_unit_id=org_unit_id, normalized_name=org_unit_name)
    priority = base_priority_for(NodeType.org_unit)
    return NodeSpec(
        node_key=key,
        type=NodeType.org_unit,
        url=url,
        org_unit_id=org_unit_id,
        org_unit_name=org_unit_name,
        status=status,
        priority_score=priority,
        base_priority=priority,
        depth=depth,
        max_attempts=settings.max_attempts,
        run_id=run_id,
        metadata=metadata,
    )


def normalize_seed_url(url: str, base: str) -> str:
    normalized = normalize_url(url, base)
    if normalized is not None:
        return normalized
    parts = urlsplit(url.strip())
    return url.strip() if parts.scheme and parts.scheme not in ("http", "https") else ""


def synthetic_org_url(name: str) -> str:
    return f"about:org_unit:{name}"


def _merge_metadata(*parts: dict[str, object] | None) -> dict[str, object]:
    merged: dict[str, object] = {}
    for part in parts:
        if not part:
            continue
        for key, value in part.items():
            merged.setdefault(key, value)
    return merged


def _probe_skip_reason(metadata: dict[str, object] | None) -> str | None:
    if not metadata:
        return None
    reason = metadata.get("probe_skip_reason")
    return reason if isinstance(reason, str) else None


def _probe_defer_reason(metadata: dict[str, object] | None) -> str | None:
    if not metadata:
        return None
    reason = metadata.get("probe_defer_reason")
    return reason if isinstance(reason, str) else None


def _defer_until_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=PROBE_DEFER_HOURS)).isoformat()


async def _apply_probe_defer(storage, node_id: int, metadata: dict[str, object]) -> None:
    """If入图前 status probe flagged this URL as a blocking-risk (5xx gateway or
    probe-side unreachable/loop), defer the node: mark ``retry`` with
    ``next_retry_at = now + PROBE_DEFER_HOURS``. ``claim_next`` skips nodes whose
    ``next_retry_at`` is in the future, so the single-threaded browser fetcher
    never attempts the broken URL this run. Next ``--resume`` re-probes; if the
    gateway recovered the URL re-enters normal pending. No-op for dead/skipped
    (no ``probe_defer_reason``) and for 429 (kept pending, throttle handles it).
    """
    reason = _probe_defer_reason(metadata)
    if reason is None:
        return
    await storage.writer.mark_node(
        node_id, NodeStatus.retry, last_error=reason, next_retry_at=_defer_until_iso()
    )


async def _seed_org_unit(
    storage,
    unit: OrgUnitSeed,
    university_url: str,
    settings,
    run_id: int,
    summary: SeedLoadSummary,
    *,
    redirect_guard: RedirectGuard | None = None,
    status_probe: StatusProbe | None = None,
) -> tuple[int, int | None]:
    org_url = normalize_seed_url(unit.url, university_url) if unit.url else synthetic_org_url(unit.name)
    if not org_url:
        return 0, None
    org_probe_skip: str | None = None
    if unit.url:
        resolved_org_url, redirect_metadata = await resolve_discovered_url(
            org_url, redirect_guard=redirect_guard, status_probe=status_probe
        )
        if resolved_org_url is None:
            org_probe_skip = _probe_skip_reason(redirect_metadata)
            if org_probe_skip is None:
                return 0, None
        else:
            org_url = resolved_org_url
    else:
        redirect_metadata = {}
    org_id = await storage.writer.upsert_org_unit(
        OrgUnitSpec(name=unit.name, url=org_url, kind=unit.kind, discovered_from_url=university_url)
    )
    summary.org_units += 1

    org_node_id: int | None = None
    if unit.url:
        node_metadata = _merge_metadata({"seeded": True}, redirect_metadata)
        node_status = NodeStatus.pending
        if org_probe_skip:
            node_status = NodeStatus.skipped
            node_metadata = _merge_metadata(node_metadata, {"reason": org_probe_skip, "probe_skipped": True})
        org_node_id = await storage.writer.upsert_node(
            org_node_spec(
                org_unit_id=org_id,
                org_unit_name=unit.name,
                url=org_url,
                settings=settings,
                run_id=run_id,
                status=node_status,
                depth=0,
                metadata=node_metadata,
            )
        )
        summary.org_unit_nodes += 1
        await _apply_probe_defer(storage, org_node_id, node_metadata)
    elif unit.faculty_urls:
        org_node_id = await storage.writer.upsert_node(
            org_node_spec(
                org_unit_id=org_id,
                org_unit_name=unit.name,
                url=org_url,
                settings=settings,
                run_id=run_id,
                status=NodeStatus.skipped,
                depth=0,
                metadata={"seeded": True, "synthetic": True, "reason": "seed_direct_faculty_urls"},
            )
        )
        summary.org_unit_nodes += 1

    faculty_entries: list[tuple[str, str]] = []
    for raw_url in unit.faculty_urls:
        faculty_url = normalize_seed_url(raw_url, org_url)
        if faculty_url:
            faculty_entries.append((raw_url, faculty_url))
    faculty_resolved = await resolve_discovered_urls(
        [fu for _, fu in faculty_entries],
        redirect_guard=redirect_guard,
        status_probe=status_probe,
    )
    for (_raw_url, faculty_url), (resolved_faculty_url, redirect_metadata) in zip(faculty_entries, faculty_resolved):
        faculty_probe_skip = _probe_skip_reason(redirect_metadata)
        if resolved_faculty_url is None and faculty_probe_skip is None:
            continue
        faculty_node_url = resolved_faculty_url or faculty_url
        node_metadata = _merge_metadata({"seeded": True, "source": "org_units[].faculty_urls"}, redirect_metadata)
        node_status = NodeStatus.pending
        if faculty_probe_skip:
            node_status = NodeStatus.skipped
            node_metadata = _merge_metadata(node_metadata, {"reason": faculty_probe_skip, "probe_skipped": True})
        faculty_id = await storage.writer.upsert_node(
            node_spec(
                NodeType.faculty_list_url,
                url=faculty_node_url,
                settings=settings,
                run_id=run_id,
                org_unit_id=org_id,
                org_unit_name=unit.name,
                depth=1 if unit.url else 0,
                metadata=node_metadata,
                status=node_status,
                subtree=True,
            )
        )
        summary.faculty_list_nodes += 1
        await _apply_probe_defer(storage, faculty_id, node_metadata)
        if org_node_id is not None:
            await storage.writer.add_edge(org_node_id, faculty_id, EdgeType.belongs_to_org_unit)
            summary.edges += 1

    return org_id, org_node_id


async def load_seed_nodes(
    university: UniversitySeed,
    storage,
    settings,
    run_id: int,
    *,
    redirect_guard: RedirectGuard | None = None,
    status_probe: StatusProbe | None = None,
) -> SeedLoadSummary:
    summary = SeedLoadSummary()
    listing_urls: list[str] = []
    for raw_url in university.org_unit_listing_urls:
        url = normalize_seed_url(raw_url, university.url)
        if url:
            listing_urls.append(url)
    listing_resolved = await resolve_discovered_urls(
        listing_urls, redirect_guard=redirect_guard, status_probe=status_probe
    )
    for listing_url, (resolved_url, redirect_metadata) in zip(listing_urls, listing_resolved):
        listing_probe_skip = _probe_skip_reason(redirect_metadata)
        if resolved_url is None and listing_probe_skip is None:
            continue
        listing_node_url = resolved_url or listing_url
        node_metadata = _merge_metadata({"seeded": True, "source": "org_unit_listing_urls"}, redirect_metadata)
        node_status = NodeStatus.pending
        if listing_probe_skip:
            node_status = NodeStatus.skipped
            node_metadata = _merge_metadata(node_metadata, {"reason": listing_probe_skip, "probe_skipped": True})
        listing_node_id = await storage.writer.upsert_node(
            node_spec(
                NodeType.org_listing_url,
                url=listing_node_url,
                settings=settings,
                run_id=run_id,
                depth=0,
                metadata=node_metadata,
                status=node_status,
            )
        )
        summary.org_listing_nodes += 1
        await _apply_probe_defer(storage, listing_node_id, node_metadata)

    for unit in university.org_units:
        await _seed_org_unit(
            storage,
            unit,
            university.url,
            settings,
            run_id,
            summary,
            redirect_guard=redirect_guard,
            status_probe=status_probe,
        )

    return summary
