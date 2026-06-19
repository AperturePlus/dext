"""HTTP status probe — 404/410 dead links, 429 rate limiting, 502/503/504 gateway (overview §7).

A best-effort backend aiohttp side-probe (same family as ``redirect.py``) that
inspects the HTTP status code of a candidate URL *before* it enters the graph
(and is reused post-fetch for retry classification). HTTP IO is an injectable
resolver returning ``(final_url, status_code)`` so the pure classification is
fully unit-testable. As with ``redirect.py``, a probe failure must NOT block
crawling — it degrades to ``probe_failed`` (treated as ok / unclear) so URLs are
never dropped just because the probe could not reach them.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dext.bridge._httputil import exception_summary as _exception_summary

logger = logging.getLogger(__name__)

DEAD = frozenset({404, 410})
RATE_LIMITED = frozenset({429})
GATEWAY = frozenset({502, 503, 504})

DEAD_CAT = "dead"
RATE_LIMITED_CAT = "rate_limited"
TRANSIENT_CAT = "transient"
OK = "ok"
PROBE_FAILED = "probe_failed"
# A probe-side *timeout* — the off-channel aiohttp probe couldn't reach the host in
# time. Distinct from PROBE_FAILED (redirect-loop / DNS / WAF): a timeout is "no
# signal", not a block signal, because the human browser (real headers / session / JS
# challenge) often loads the same URL fine. Callers must NOT defer/skip/drop on a
# timeout — the URL should still be fetched by the browser.
PROBE_TIMEOUT = "probe_timeout"

Resolver = Callable[[str], Awaitable[tuple[str, int]]]


@dataclass(frozen=True)
class StatusVerdict:
    category: str
    status_code: int | None = None
    reason: str | None = None


def classify_status(status_code: int | None) -> str:
    """Pure status-code → category classification.

    ``None`` (e.g. resolver returned no status, or HEAD unsupported) degrades to
    ``OK`` — unknown is never treated as dead to avoid dropping live URLs.
    """
    if status_code is None:
        return OK
    if status_code in DEAD:
        return DEAD_CAT
    if status_code in RATE_LIMITED:
        return RATE_LIMITED_CAT
    if status_code in GATEWAY:
        return TRANSIENT_CAT
    return OK


def _reason_for(status_code: int | None) -> str:
    if status_code is None:
        return "probe_failed"
    return f"http_{status_code}"


async def _aiohttp_resolver(url: str, *, timeout: float = 5.0) -> tuple[str, int]:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(url, allow_redirects=True) as resp:
            return str(resp.url), resp.status


class StatusProbe:
    def __init__(self, *, resolver: Resolver | None = None, timeout: float = 3.0) -> None:
        self._timeout = timeout
        self._resolver = resolver or _aiohttp_resolver

    async def probe(self, url: str) -> StatusVerdict:
        """Best-effort status probe; failure does NOT block crawling.

        A resolver *timeout* degrades to ``PROBE_TIMEOUT`` (status_code None,
        reason ``probe_timeout``) — "no signal", not a block: callers keep crawling
        the URL (the human browser often loads a slow-to-probe host fine). Other
        resolver errors (redirect-loop / DNS / WAF) degrade to ``PROBE_FAILED``,
        which callers may treat as a block-risk (e.g. defer). A failed probe must
        never be interpreted as dead — only a real ``DEAD`` status code drops/skips.
        ``TimeoutError`` is caught before ``Exception`` so timeouts are not swallowed
        by the generic probe_failed path.
        """
        try:
            _final_url, status_code = await self._resolver(url)
        except TimeoutError as exc:  # aiohttp total timeout (asyncio.TimeoutError == builtin on 3.11+)
            logger.info("status probe timeout url=%s %s", url, _exception_summary(exc))
            return StatusVerdict(PROBE_TIMEOUT, status_code=None, reason="probe_timeout")
        except Exception as exc:  # WAF / DNS / redirect loop
            logger.info("status probe failed url=%s %s", url, _exception_summary(exc))
            return StatusVerdict(PROBE_FAILED, status_code=None, reason="probe_failed")
        category = classify_status(status_code)
        return StatusVerdict(category, status_code=status_code, reason=_reason_for(status_code))
