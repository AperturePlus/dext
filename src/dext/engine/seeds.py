"""Seed loading and graph-node construction helpers for SP6."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from dext.bridge.redirect import BLOCKED, PROBE_FAILED, RedirectGuard
from dext.page.urls import normalize_url
from dext.seed import OrgUnitSeed, UniversitySeed
from dext.storage.dedup import node_key_for
from dext.storage.models import EdgeType, NodeStatus, NodeType
from dext.storage.writer import NodeSpec, OrgUnitSpec
from dext.url_policy import is_allowed_fetch_host
from dext.engine.priorities import BASE_PRIORITY_BY_TYPE, priority_for as base_priority_for, subtree_priority_for

PRIORITY_BY_TYPE: dict[NodeType, float] = BASE_PRIORITY_BY_TYPE


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
) -> tuple[str | None, dict[str, object]]:
    if redirect_guard is None:
        final_url = url
        metadata: dict[str, object] = {"source_url": url}
    else:
        verdict = await redirect_guard.probe_redirect(url)
        if verdict.verdict in {BLOCKED, PROBE_FAILED}:
            return None, {}
        final_url = verdict.final_url or url
        metadata = {"source_url": url}
        if final_url != url:
            metadata["redirect_verdict"] = verdict.verdict
            if verdict.reason:
                metadata["redirect_reason"] = verdict.reason
    if not is_allowed_fetch_host(final_url):
        return None, {}
    return final_url, metadata


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


async def _seed_org_unit(
    storage,
    unit: OrgUnitSeed,
    university_url: str,
    settings,
    run_id: int,
    summary: SeedLoadSummary,
    *,
    redirect_guard: RedirectGuard | None = None,
) -> tuple[int, int | None]:
    org_url = normalize_seed_url(unit.url, university_url) if unit.url else synthetic_org_url(unit.name)
    if not org_url:
        return 0, None
    if unit.url:
        resolved_org_url, redirect_metadata = await resolve_discovered_url(org_url, redirect_guard=redirect_guard)
        if resolved_org_url is None:
            return 0, None
        org_url = resolved_org_url
    else:
        redirect_metadata = {}
    org_id = await storage.writer.upsert_org_unit(
        OrgUnitSpec(name=unit.name, url=org_url, kind=unit.kind, discovered_from_url=university_url)
    )
    summary.org_units += 1

    org_node_id: int | None = None
    if unit.url:
        org_node_id = await storage.writer.upsert_node(
            org_node_spec(
                org_unit_id=org_id,
                org_unit_name=unit.name,
                url=org_url,
                settings=settings,
                run_id=run_id,
                depth=0,
                metadata=_merge_metadata({"seeded": True}, redirect_metadata),
            )
        )
        summary.org_unit_nodes += 1
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

    for raw_url in unit.faculty_urls:
        faculty_url = normalize_seed_url(raw_url, org_url)
        if not faculty_url:
            continue
        resolved_faculty_url, redirect_metadata = await resolve_discovered_url(
            faculty_url,
            redirect_guard=redirect_guard,
        )
        if resolved_faculty_url is None:
            continue
        faculty_id = await storage.writer.upsert_node(
            node_spec(
                NodeType.faculty_list_url,
                url=resolved_faculty_url,
                settings=settings,
                run_id=run_id,
                org_unit_id=org_id,
                org_unit_name=unit.name,
                depth=1 if unit.url else 0,
                metadata=_merge_metadata({"seeded": True, "source": "org_units[].faculty_urls"}, redirect_metadata),
                subtree=True,
            )
        )
        summary.faculty_list_nodes += 1
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
) -> SeedLoadSummary:
    summary = SeedLoadSummary()
    for raw_url in university.org_unit_listing_urls:
        url = normalize_seed_url(raw_url, university.url)
        if not url:
            continue
        resolved_url, redirect_metadata = await resolve_discovered_url(url, redirect_guard=redirect_guard)
        if resolved_url is None:
            continue
        await storage.writer.upsert_node(
            node_spec(
                NodeType.org_listing_url,
                url=resolved_url,
                settings=settings,
                run_id=run_id,
                depth=0,
                metadata=_merge_metadata({"seeded": True, "source": "org_unit_listing_urls"}, redirect_metadata),
            )
        )
        summary.org_listing_nodes += 1

    for unit in university.org_units:
        await _seed_org_unit(
            storage,
            unit,
            university.url,
            settings,
            run_id,
            summary,
            redirect_guard=redirect_guard,
        )

    return summary
