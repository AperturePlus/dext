"""HumanFetcherBridge — the GraphDriver's single fetch entry point.

`fetch()` enqueues a job and awaits the matching /complete (or /fail /skip /timeout).
All job-resolution methods are idempotent against stale (timed-out) job ids: they log
and no-op rather than raise, so a late /complete after a 60 s timeout can't crash the
server. Thin-handler/fat-bridge: server.py holds no job logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from dext.bridge.health import FrontendHealth, FrontendHealthSnapshot
from dext.bridge.mojibake import repair_mojibake_text
from dext.bridge.queue import (
    FetchJob, FetchQueue, JobContext, JobStatus, QueueStats, new_job_id, utcnow,
)
from dext.types import FetchAction, FetchResult, PaginationState
from dext.url_policy import has_explicit_port, is_allowed_fetch_host

if TYPE_CHECKING:
    from dext.config import Settings

logger = logging.getLogger(__name__)

FETCH_WAIT_POLL_SECONDS = 0.05


class HumanFetcherBridge:
    def __init__(self, settings: "Settings") -> None:
        self._timeout = settings.fetch_timeout_seconds
        self._queue = FetchQueue()
        self._frontend_health = FrontendHealth()

    async def fetch(self, *, url: str, context: JobContext,
                    identity_url: str | None = None,
                    action: FetchAction | None = None) -> FetchResult:
        if not is_allowed_fetch_host(url):
            logger.info("rejecting fetch with disallowed host url=%s", url)
            return FetchResult(
                identity_url=identity_url or url, requested_url=url, final_url=url,
                status_code=None, html="", title="", pagination_states=[],
                block_reason="invalid_url:host_not_allowed",
            )
        loop = asyncio.get_running_loop()
        job = FetchJob(
            id=new_job_id(), url=url, context=context, created_at=utcnow(),
            timeout_seconds=self._timeout, identity_url=identity_url, action=action,
            future=loop.create_future(),
        )
        self._queue.enqueue(job)
        return await self._wait_for_job(job)

    # ---- job-resolution ops (called by server handlers) ----

    def next_job(self) -> FetchJob | None:
        return self._queue.take_next()

    def complete(self, job_id: str, *, html: str, final_url: str, title: str,
                 pagination_states: list[PaginationState] | None = None) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        if has_explicit_port(final_url):
            logger.info("rejecting complete for job %s with explicit-port final_url=%s", job_id, final_url)
            job.future.set_result(self._failed_result(job, block_reason="invalid_url:explicit_port"))
            self._queue.finish(job, JobStatus.failed)
            return True
        result = FetchResult(
            identity_url=job.identity_url or job.url, requested_url=job.url, final_url=final_url,
            status_code=None, html=repair_mojibake_text(html), title=repair_mojibake_text(title),
            pagination_states=pagination_states or [], block_reason=None,
        )
        job.future.set_result(result)
        self._queue.finish(job, JobStatus.completed)
        return True

    def fail(self, job_id: str, message: str) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        job.future.set_result(self._failed_result(job, block_reason=message.strip() or "human_failed"))
        self._queue.finish(job, JobStatus.failed)
        return True

    def skip(self, job_id: str, *, reason: str | None = None) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        block_reason = (reason or "").strip() or "human_skip"
        job.future.set_result(self._failed_result(job, block_reason=block_reason))
        self._queue.finish(job, JobStatus.skipped)
        return True

    def override(self, job_id: str, new_url: str) -> FetchJob | None:
        """Swap the in-flight job's URL, keeping the same id/future/assigned status."""
        if has_explicit_port(new_url):
            logger.info("rejecting override for job %s with explicit-port url=%s", job_id, new_url)
            return None
        if not is_allowed_fetch_host(new_url):
            logger.info("rejecting override for job %s with disallowed host url=%s", job_id, new_url)
            return None
        job = self._queue.find(job_id)
        if job is None:
            return None
        job.url = new_url
        return job

    def stats(self) -> QueueStats:
        return self._queue.stats()

    def current_job(self) -> FetchJob | None:
        return self._queue.assigned

    def record_frontend_heartbeat(
        self,
        *,
        owner_tab_id: str,
        url: str,
        current_job_id: str | None = None,
        auto_mode: bool = False,
        paused: bool = False,
        client_timestamp_ms: float | None = None,
    ) -> None:
        self._frontend_health.record(
            owner_tab_id=owner_tab_id,
            url=url,
            current_job_id=current_job_id,
            auto_mode=auto_mode,
            paused=paused,
            client_timestamp_ms=client_timestamp_ms,
        )

    def frontend_health(self) -> FrontendHealthSnapshot:
        return self._frontend_health.snapshot()

    # ---- internals ----

    async def _wait_for_job(self, job: FetchJob) -> FetchResult:
        future = job.future
        if future is None:
            raise RuntimeError("fetch job missing future")

        active_elapsed = 0.0
        last_tick = time.monotonic()
        timeout_counting = self._frontend_health.is_alive(now=last_tick)
        if not timeout_counting:
            logger.info("frontend heartbeat stale/absent; pausing timeout for fetch job %s", job.id)
        while True:
            if future.done():
                return future.result()

            now = time.monotonic()
            if timeout_counting:
                active_elapsed += now - last_tick
                if active_elapsed >= self._timeout:
                    self._queue.discard(job)
                    logger.warning(
                        "fetch job %s timed out after %ss of healthy frontend time (url=%s)",
                        job.id,
                        self._timeout,
                        job.url,
                    )
                    return FetchResult(
                        identity_url=job.identity_url or job.url,
                        requested_url=job.url,
                        final_url=job.url,
                        status_code=None,
                        html="",
                        title="",
                        pagination_states=[],
                        block_reason="timeout",
                    )

            alive_now = self._frontend_health.is_alive(now=now)
            if alive_now and not timeout_counting:
                logger.info("frontend heartbeat restored; resuming timeout for fetch job %s", job.id)
            elif not alive_now and timeout_counting:
                logger.info("frontend heartbeat stale/absent; pausing timeout for fetch job %s", job.id)
            timeout_counting = alive_now

            last_tick = now
            sleep_seconds = FETCH_WAIT_POLL_SECONDS
            if timeout_counting:
                sleep_seconds = min(sleep_seconds, max(0.0, self._timeout - active_elapsed))
            done, _ = await asyncio.wait({future}, timeout=sleep_seconds)
            if done:
                return future.result()

    def _resolvable(self, job_id: str) -> FetchJob | None:
        job = self._queue.find(job_id)
        if job is None:
            logger.info("ignoring call for unknown/stale job id %s", job_id)
            return None
        if job.future is None or job.future.done():
            logger.info("ignoring late call for already-resolved job %s", job_id)
            return None
        return job

    def _failed_result(self, job: FetchJob, *, block_reason: str) -> FetchResult:
        return FetchResult(
            identity_url=job.identity_url or job.url, requested_url=job.url, final_url=job.url,
            status_code=None, html="", title="", pagination_states=[], block_reason=block_reason,
        )
