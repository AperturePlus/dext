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

logger = logging.getLogger(__name__)

# wechat / QQ public-account article hosts that signal a noise-page redirect trap.
DEFAULT_BLACKLIST: frozenset[str] = frozenset({"mp.weixin.qq.com", "weixin.qq.com"})

OK = "ok"
BLOCKED = "blocked"
OFFSITE_OK = "offsite_ok"
PROBE_FAILED = "probe_failed"

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


def _exception_summary(exc: Exception) -> str:
    error = type(exc).__name__
    if error == "TooManyRedirects":
        history = getattr(exc, "history", None)
        try:
            redirects = len(history) if history is not None else None
        except TypeError:
            redirects = None
        return f"error={error} redirects={redirects}" if redirects is not None else f"error={error}"

    message = " ".join(str(exc).split())
    if not message:
        return f"error={error}"
    if len(message) > 160:
        message = f"{message[:157]}..."
    return f"error={error} message={message}"


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

        Returns ``PROBE_FAILED`` on resolver errors (WAF / timeout / DNS / loop).
        Callers decide what to do with a failed probe — historically a failed
        probe must NOT cause the URL to be dropped, only a ``BLOCKED`` verdict
        (wechat trap) should.
        """
        try:
            final_url = await self._resolver(url)
        except Exception as exc:  # WAF / timeout / DNS / redirect loop
            logger.info("redirect probe failed url=%s %s", url, _exception_summary(exc))
            return RedirectVerdict(PROBE_FAILED, reason="probe_failed")
        return classify_redirect(url, final_url, blacklist=self._blacklist)
