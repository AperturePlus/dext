"""Refreshable fail-closed provider for the last ready ACTIVE snapshot."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from time import monotonic

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import RecommendationRuntimeError
from dext_recommend.readiness import ActiveBuildSnapshot, ReadinessReport, ReadinessService

logger = logging.getLogger(__name__)


class LiveActiveSnapshotProvider:
    def __init__(
        self, readiness: ReadinessService, settings: RecommendSettings,
    ) -> None:
        self._readiness = readiness
        self._settings = settings
        self._cached: ActiveBuildSnapshot | None = None
        self._cached_at = 0.0
        self._last_report: ReadinessReport | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._closed = False
        self._refresh_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._closed:
            raise RecommendationRuntimeError(
                code="readiness_failed", message="snapshot provider is closed", retryable=True,
            )
        report = await self._refresh_once()
        if not report.ready or report.snapshot is None:
            raise RecommendationRuntimeError(
                code="readiness_failed",
                message="recommendation readiness check failed",
                retryable=True,
            )

    def start_refresh(self) -> None:
        """Start the background loop after all other runtime assembly succeeds."""
        if self._closed:
            raise RecommendationRuntimeError(
                code="readiness_failed", message="snapshot provider is closed", retryable=True,
            )
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(), name="dext-recommend-snapshot-refresh",
            )

    async def _refresh_once(self) -> ReadinessReport:
        report = await self._readiness.check()
        self._last_report = report
        if report.ready and report.snapshot is not None:
            self._cached = report.snapshot
            self._cached_at = monotonic()
        return report

    async def _refresh_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._settings.runtime_refresh_interval)
                if self._closed:
                    break
                async with self._refresh_lock:
                    await self._refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep last good snapshot until it becomes stale
                logger.warning(
                    "readiness refresh failed error_type=%s", type(exc).__name__,
                )

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        if self._closed or self._cached is None:
            return None
        if monotonic() - self._cached_at > self._settings.runtime_snapshot_max_age:
            return None
        return self._cached

    def last_report(self) -> ReadinessReport | None:
        return self._last_report

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        task, self._refresh_task = self._refresh_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


__all__ = ["LiveActiveSnapshotProvider"]
