"""Process-local frontend heartbeat tracking for the fetch bridge."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


DEFAULT_FRONTEND_HEARTBEAT_STALE_SECONDS = 15.0


@dataclass(frozen=True)
class FrontendHealthSnapshot:
    alive: bool
    last_seen_seconds_ago: float | None
    owner_tab_id: str | None
    url: str | None
    current_job_id: str | None
    auto_mode: bool
    paused: bool
    client_timestamp_ms: float | None
    stale_after_seconds: float


@dataclass
class FrontendHeartbeat:
    owner_tab_id: str
    url: str
    current_job_id: str | None
    auto_mode: bool
    paused: bool
    client_timestamp_ms: float | None
    received_at: float


class FrontendHealth:
    """Tracks the most recent userscript owner heartbeat."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = DEFAULT_FRONTEND_HEARTBEAT_STALE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stale_after_seconds = stale_after_seconds
        self._clock = clock
        self._latest: FrontendHeartbeat | None = None

    @property
    def stale_after_seconds(self) -> float:
        return self._stale_after_seconds

    def record(
        self,
        *,
        owner_tab_id: str,
        url: str,
        current_job_id: str | None = None,
        auto_mode: bool = False,
        paused: bool = False,
        client_timestamp_ms: float | None = None,
    ) -> None:
        self._latest = FrontendHeartbeat(
            owner_tab_id=owner_tab_id,
            url=url,
            current_job_id=current_job_id,
            auto_mode=auto_mode,
            paused=paused,
            client_timestamp_ms=client_timestamp_ms,
            received_at=self._clock(),
        )

    def last_seen_at(self) -> float | None:
        return None if self._latest is None else self._latest.received_at

    def is_alive(self, *, now: float | None = None) -> bool:
        if self._latest is None:
            return False
        checked_at = self._clock() if now is None else now
        return checked_at - self._latest.received_at <= self._stale_after_seconds

    def snapshot(self) -> FrontendHealthSnapshot:
        latest = self._latest
        if latest is None:
            return FrontendHealthSnapshot(
                alive=False,
                last_seen_seconds_ago=None,
                owner_tab_id=None,
                url=None,
                current_job_id=None,
                auto_mode=False,
                paused=False,
                client_timestamp_ms=None,
                stale_after_seconds=self._stale_after_seconds,
            )
        now = self._clock()
        age = max(0.0, now - latest.received_at)
        return FrontendHealthSnapshot(
            alive=age <= self._stale_after_seconds,
            last_seen_seconds_ago=age,
            owner_tab_id=latest.owner_tab_id,
            url=latest.url,
            current_job_id=latest.current_job_id,
            auto_mode=latest.auto_mode,
            paused=latest.paused,
            client_timestamp_ms=latest.client_timestamp_ms,
            stale_after_seconds=self._stale_after_seconds,
        )
