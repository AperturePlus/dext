# src/dext_recommend/core/detail_fetch.py
"""Bounded fan-out of ProfessorFactPort.get_detail over the detail rerank window.

get_detail is single-entity by protocol; core never calls a non-existent
batch method. Missing details return None; callers mark weak_explanation.

W6-b (R3 hardening): each get_detail is wrapped with the request-local
execution context. KeyError/LookupError -> None (no error code, just missing).
Any other exception -> None + a `details_unavailable` phase diagnostic
(partial degradation: the request still succeeds, the caller emits a
DETAILS_UNAVAILABLE warning for failed entities in the rerank window).
CancelledError is re-raised so asyncio.wait_for / gather can propagate it.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

from dext_recommend.ports.professor_facts import ProfessorDetail, ProfessorFactPort, ViewerPermissions
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core._resilience import RecommendExecutionContext


def _retryable_detail_error(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, sqlite3.OperationalError, OSError))


async def _fetch_one(
    facts_port: ProfessorFactPort,
    snapshot: ActiveBuildSnapshot,
    entity_id: str,
    include_contacts: bool,
    viewer_permissions: ViewerPermissions,
    ctx: RecommendExecutionContext,
    semaphore: asyncio.Semaphore,
) -> tuple[str, ProfessorDetail | None, bool]:
    """Return (entity_id, detail_or_None, failed). `failed` is True only for
    operational failures (not KeyError/LookupError)."""
    async with semaphore:
        for attempt in (1, 2):
            start = time.perf_counter()
            try:
                detail = await facts_port.get_detail(
                    snapshot, entity_id, include_contacts, viewer_permissions,
                )
            except (KeyError, LookupError):
                elapsed = (time.perf_counter() - start) * 1000
                ctx.record("details", elapsed, None, attempt=attempt)
                return entity_id, None, False
            except asyncio.CancelledError:
                elapsed = (time.perf_counter() - start) * 1000
                ctx.record("details", elapsed, "cancelled", attempt=attempt)
                raise
            except Exception as exc:
                elapsed = (time.perf_counter() - start) * 1000
                if attempt == 1 and _retryable_detail_error(exc):
                    ctx.record("details", elapsed, "details_retry", attempt=attempt)
                    await asyncio.sleep(0)
                    continue
                ctx.record("details", elapsed, "details_unavailable", attempt=attempt)
                ctx.record(
                    f"details:{entity_id}",
                    elapsed,
                    f"details_unavailable:{type(exc).__name__}",
                    attempt=attempt,
                )
                return entity_id, None, True
            elapsed = (time.perf_counter() - start) * 1000
            ctx.record("details", elapsed, None, attempt=attempt)
            return entity_id, detail, False
    return entity_id, None, True


async def _fetch_batch(
    facts_port: ProfessorFactPort,
    snapshot: ActiveBuildSnapshot,
    entity_ids: list[str],
    include_contacts: bool,
    viewer_permissions: ViewerPermissions,
    ctx: RecommendExecutionContext,
) -> tuple[dict[str, ProfessorDetail | None], set[str]] | None:
    getter = getattr(facts_port, "get_details", None)
    if not callable(getter):
        return None
    for attempt in (1, 2):
        start = time.perf_counter()
        try:
            details = await getter(
                snapshot, entity_ids, include_contacts, viewer_permissions,
            )
        except (KeyError, LookupError):
            elapsed = (time.perf_counter() - start) * 1000
            ctx.record("details_batch", elapsed, None, attempt=attempt)
            return {eid: None for eid in entity_ids}, set()
        except asyncio.CancelledError:
            elapsed = (time.perf_counter() - start) * 1000
            ctx.record("details_batch", elapsed, "cancelled", attempt=attempt)
            raise
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            if attempt == 1 and _retryable_detail_error(exc):
                ctx.record("details_batch", elapsed, "details_retry", attempt=attempt)
                await asyncio.sleep(0)
                continue
            error = f"details_unavailable:{type(exc).__name__}"
            ctx.record("details_batch", elapsed, error, attempt=attempt)
            for entity_id in entity_ids:
                ctx.record(f"details:{entity_id}", elapsed, error, attempt=attempt)
            return {eid: None for eid in entity_ids}, set(entity_ids)
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record("details_batch", elapsed, None, attempt=attempt)
        return {eid: details.get(eid) for eid in entity_ids}, set()
    return {eid: None for eid in entity_ids}, set(entity_ids)


async def fetch_details(
    snapshot: ActiveBuildSnapshot,
    facts_port: ProfessorFactPort,
    entity_ids: list[str],
    *,
    include_contacts: bool,
    viewer_permissions: ViewerPermissions,
    concurrency: int,
    ctx: RecommendExecutionContext,
) -> tuple[dict[str, ProfessorDetail | None], set[str]]:
    """Return (detail_map, failed_set). `failed_set` holds entity_ids whose
    get_detail raised an operational error (not missing)."""
    batch = await _fetch_batch(
        facts_port, snapshot, entity_ids, include_contacts,
        viewer_permissions, ctx,
    )
    if batch is not None:
        return batch

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _guarded(eid: str) -> tuple[str, ProfessorDetail | None, bool]:
        return await _fetch_one(
            facts_port, snapshot, eid, include_contacts, viewer_permissions,
            ctx, semaphore,
        )

    triples = await asyncio.gather(*(_guarded(eid) for eid in entity_ids))
    detail_map = {eid: detail for eid, detail, _failed in triples}
    failed = {eid for eid, detail, failed in triples if failed}
    return detail_map, failed


__all__ = ["fetch_details"]
