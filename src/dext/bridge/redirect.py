"""Best-effort redirect / wechat-公众号 guard (overview §7).

SP6 may optionally call probe_redirect() before enqueueing a detail candidate to
drop obvious wechat traps or redirect probes that fail. The HTTP IO is an
injectable resolver so the (pure) host classification is fully unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from dext.bridge._httputil import exception_summary as _exception_summary

logger = logging.getLogger(__name__)

# wechat / QQ public-account article hosts that signal a noise-page redirect trap.
DEFAULT_BLACKLIST: frozenset[str] = frozenset({"mp.weixin.qq.com", "weixin.qq.com"})

OK = "ok"
BLOCKED = "blocked"
OFFSITE_OK = "offsite_ok"
PROBE_FAILED = "probe_failed"
# A probe-side *timeout* — the off-channel aiohttp probe couldn't resolve redirects
# in time. Distinct from PROBE_FAILED (redirect-loop / DNS / WAF): a timeout is "no
# signal", not a drop. The browser may still load the URL, so callers must NOT drop a
# URL on a timeout (same as PROBE_FAILED today); the verdict just carries a more
# accurate reason code for diagnostics.
PROBE_TIMEOUT = "probe_timeout"

Resolver = Callable[[str], Awaitable[str]]


@dataclass
class RedirectVerdict:
    verdict: str
    final_url: str | None = None
    final_host: str | None = None
    reason: str | None = None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def classify_redirect(requested_url: str, final_url: str, *,
                      blacklist: frozenset[str] = DEFAULT_BLACKLIST) -> RedirectVerdict:
    """Pure host-based classification: blacklisted final host → blocked; a different
    host → offsite_ok (personal homepage is legitimate); same host → ok."""
    final_host = _host(final_url)
    if final_host in blacklist:
        return RedirectVerdict(BLOCKED, final_url=final_url, final_host=final_host, reason="wechat_redirect")
    if final_host and final_host != _host(requested_url):
        return RedirectVerdict(OFFSITE_OK, final_url=final_url, final_host=final_host)
    return RedirectVerdict(OK, final_url=final_url, final_host=final_host)


async def _aiohttp_resolver(url: str, *, timeout: float = 3.0) -> str:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(url, allow_redirects=True) as resp:
            return str(resp.url)


class RedirectGuard:
    def __init__(self, *, blacklist: frozenset[str] = DEFAULT_BLACKLIST,
                 resolver: Resolver | None = None) -> None:
        self._blacklist = blacklist
        self._resolver = resolver or _aiohttp_resolver

    async def probe_redirect(self, url: str) -> RedirectVerdict:
        """Best-effort redirect probe; failure does NOT block crawling.

        Returns ``PROBE_TIMEOUT`` on a resolver *timeout* (reason ``probe_timeout``)
        and ``PROBE_FAILED`` on other resolver errors (WAF / DNS / redirect loop).
        Neither drops the URL — only a ``BLOCKED`` verdict (wechat trap) should. A
        timeout is "no signal" (the browser may still load the URL); the distinct
        verdict just gives a more accurate reason code. ``TimeoutError`` is caught
        before ``Exception`` so timeouts are not swallowed by probe_failed.
        """
        try:
            final_url = await self._resolver(url)
        except TimeoutError as exc:  # aiohttp total timeout (asyncio.TimeoutError == builtin on 3.11+)
            logger.info("redirect probe timeout url=%s %s", url, _exception_summary(exc))
            return RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")
        except Exception as exc:  # WAF / DNS / redirect loop
            logger.info("redirect probe failed url=%s %s", url, _exception_summary(exc))
            return RedirectVerdict(PROBE_FAILED, reason="probe_failed")
        return classify_redirect(url, final_url, blacklist=self._blacklist)
