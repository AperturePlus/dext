from dext.bridge.probe import (
    DEAD_CAT, OK, PROBE_FAILED, PROBE_TIMEOUT, RATE_LIMITED_CAT, TRANSIENT_CAT, StatusProbe, classify_status,
)


def test_classify_status_dead_links():
    assert classify_status(404) == DEAD_CAT
    assert classify_status(410) == DEAD_CAT


def test_classify_status_rate_limited():
    assert classify_status(429) == RATE_LIMITED_CAT


def test_classify_status_gateway_transient():
    assert classify_status(502) == TRANSIENT_CAT
    assert classify_status(503) == TRANSIENT_CAT
    assert classify_status(504) == TRANSIENT_CAT


def test_classify_status_ok_for_none_and_2xx_3xx():
    assert classify_status(None) == OK
    assert classify_status(200) == OK
    assert classify_status(301) == OK
    assert classify_status(500) == OK


async def test_probe_dead_via_resolver():
    async def resolver(url):
        return url, 404

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == DEAD_CAT
    assert v.status_code == 404
    assert v.reason == "http_404"


async def test_probe_rate_limited_via_resolver():
    async def resolver(url):
        return url, 429

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == RATE_LIMITED_CAT
    assert v.status_code == 429
    assert v.reason == "http_429"


async def test_probe_transient_via_resolver():
    async def resolver(url):
        return url, 503

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == TRANSIENT_CAT
    assert v.status_code == 503
    assert v.reason == "http_503"


async def test_probe_ok_via_resolver():
    async def resolver(url):
        return url, 200

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == OK
    assert v.status_code == 200


async def test_probe_failure_returns_probe_failed_not_dead():
    async def resolver(url):
        raise RuntimeError("WAF")

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == PROBE_FAILED
    assert v.status_code is None
    assert v.reason == "probe_failed"


async def test_probe_timeout_returns_probe_timeout_not_probe_failed():
    # A backend side-probe timeout means "this off-channel probe couldn't reach the
    # host in time" — NOT that the URL is dead or that the browser fetcher will hang.
    # The human browser (real headers / session / JS challenge) often loads the same
    # URL fine, so a probe timeout must degrade to a non-blocking "no signal" verdict
    # distinct from probe_failed (loop/DNS/WAF, which more likely also block browsers).
    async def resolver(url):
        raise TimeoutError("total timeout")

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == PROBE_TIMEOUT
    assert v.category != PROBE_FAILED
    assert v.status_code is None
    assert v.reason == "probe_timeout"


async def test_probe_asyncio_timeout_returns_probe_timeout():
    # asyncio.TimeoutError is the builtin TimeoutError on 3.11+; the aiohttp
    # ClientTimeout(total=…) total-timeout path raises it.
    import asyncio

    async def resolver(url):
        raise asyncio.TimeoutError()

    v = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")
    assert v.category == PROBE_TIMEOUT
    assert v.reason == "probe_timeout"


async def test_probe_timeout_logs_compact_exception_summary(caplog):
    async def resolver(url):
        raise TimeoutError("total timeout")

    with caplog.at_level("INFO", logger="dext.bridge.probe"):
        verdict = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")

    assert verdict.category == PROBE_TIMEOUT
    assert "status probe timeout url=https://x.edu.cn/p error=TimeoutError" in caplog.text


async def test_probe_failure_logs_compact_exception_summary(caplog):
    async def resolver(url):
        raise RuntimeError("WAF blocked")

    with caplog.at_level("INFO", logger="dext.bridge.probe"):
        verdict = await StatusProbe(resolver=resolver).probe("https://x.edu.cn/p")

    assert verdict.category == PROBE_FAILED
    assert "status probe failed url=https://x.edu.cn/p error=RuntimeError message=WAF blocked" in caplog.text


async def test_probe_too_many_redirects_log_omits_verbose_history(caplog):
    class FakeTooManyRedirects(Exception):
        history = ["<ClientResponse(...) [302 None]>\n<CIMultiDictProxy(...)>"] * 10

    FakeTooManyRedirects.__name__ = "TooManyRedirects"

    async def resolver(url):
        raise FakeTooManyRedirects("verbose redirect history")

    with caplog.at_level("INFO", logger="dext.bridge.probe"):
        verdict = await StatusProbe(resolver=resolver).probe("https://sirpa.fudan.edu.cn/_redirect")

    assert verdict.category == PROBE_FAILED
    assert "error=TooManyRedirects redirects=10" in caplog.text
    assert "ClientResponse" not in caplog.text
    assert "CIMultiDictProxy" not in caplog.text


# Regression (real HTTP): https://bs.nankai.edu.cn/2022/0101/c13654a499696/page.psp
# currently responds with a redirect loop ("err too many redirects"). The probe
# must NOT drop or dead-flag the URL — it degrades to PROBE_FAILED so the URL
# still enters the graph and is fetched normally by the userscript path. No mocks:
# real aiohttp against the site, skipped when the network is unavailable.
_NANKAI_TOO_MANY_REDIRECTS_URL = (
    "https://bs.nankai.edu.cn/2022/0101/c13654a499696/page.psp"
)


async def test_probe_nankai_too_many_redirects_returns_probe_failed_not_dead(live_http):
    verdict = await StatusProbe().probe(_NANKAI_TOO_MANY_REDIRECTS_URL)
    assert verdict.category == PROBE_FAILED
    assert verdict.status_code is None
    assert verdict.reason == "probe_failed"


async def test_probe_nankai_too_many_redirects_logs_compact_summary_without_history(live_http, caplog):
    with caplog.at_level("INFO", logger="dext.bridge.probe"):
        verdict = await StatusProbe().probe(_NANKAI_TOO_MANY_REDIRECTS_URL)

    assert verdict.category == PROBE_FAILED
    assert f"status probe failed url={_NANKAI_TOO_MANY_REDIRECTS_URL}" in caplog.text
    # verbose redirect history (ClientResponse/CIMultiDictProxy reprs) must be
    # collapsed to a redirect count — never reach logs.
    assert "error=TooManyRedirects redirects=" in caplog.text
    assert "ClientResponse" not in caplog.text
    assert "CIMultiDictProxy" not in caplog.text


async def test_resolve_discovered_url_nankai_too_many_redirects_is_deferred(live_http):
    """入图前 integration: a too-many-redirects URL is deferred (probe_failed →
    probe_defer_reason), not dropped and not dead-flagged, so the browser doesn't
    hang on the redirect loop this run.
    """
    from dext.engine.seeds import resolve_discovered_url

    probe = StatusProbe()
    resolved, metadata = await resolve_discovered_url(
        _NANKAI_TOO_MANY_REDIRECTS_URL, status_probe=probe
    )
    assert resolved == _NANKAI_TOO_MANY_REDIRECTS_URL  # not dropped
    assert metadata["source_url"] == _NANKAI_TOO_MANY_REDIRECTS_URL
    assert metadata["probe_defer_reason"] == "probe_failed"  # deferred, not dead
    assert "probe_skip_reason" not in metadata  # not dead-flagged


# Regression (real HTTP): https://ibs.nankai.edu.cn/renbing redirects through
# bs.nankai.edu.cn and lands at http://222.30.60.20/renbing — a gateway-side
# failure. In a browser the IP hop serves a 502 page ("当前无法处理此请求。err 502");
# from the test process the off-channel probe is currently slow/unreachable and
# times out, which aiohttp surfaces as a TimeoutError → PROBE_TIMEOUT. All of these
# are NON-dead outcomes: a real captured 502 → transient (deferred for retry); a
# probe timeout → probe_timeout (NO defer — the human browser may still load it,
# see PROBE_TIMEOUT design). 入图前 the URL must stay pending rather than be
# dropped/flagged http_404. No mocks: real aiohttp follows the full redirect chain;
# skipped when the network is unavailable.
_IBS_NANKAI_GATEWAY_URL = "https://ibs.nankai.edu.cn/renbing"
# Categories that mean "not dead" — gateway transient (real 502 captured), a
# probe-side failure (loop/DNS/WAF), or a probe timeout all keep the URL alive;
# only DEAD_CAT (404/410) would drop/skip it.
_NON_DEAD_CATEGORIES = {TRANSIENT_CAT, PROBE_FAILED, PROBE_TIMEOUT}


async def test_probe_ibs_nankai_gateway_failure_is_not_dead(live_http):
    verdict = await StatusProbe().probe(_IBS_NANKAI_GATEWAY_URL)
    assert verdict.category in _NON_DEAD_CATEGORIES, (
        f"gateway failure URL must not be classified dead; got {verdict}"
    )
    # If the gateway actually surfaced a status (502-class), it must be transient;
    # if the chain timed out, category is probe_timeout with no status.
    if verdict.status_code is not None:
        assert verdict.category == TRANSIENT_CAT


async def test_resolve_discovered_url_ibs_nankai_gateway_failure_is_not_blocked(live_http):
    """入图前 integration: the ibs.nankai gateway-failure URL is NOT dead and NOT
    dropped. If the gateway surfaces a real 502 it is deferred (transient→http_502);
    if the off-channel probe times out it is kept pending with NO defer
    (probe_timeout → the human browser may still load a slow-to-probe host, so the
    URL must reach the fetcher, not be shelved for 24h). Either way it is never
    dead-flagged and never dropped.
    """
    from dext.engine.seeds import resolve_discovered_url

    probe = StatusProbe()
    resolved, metadata = await resolve_discovered_url(
        _IBS_NANKAI_GATEWAY_URL, status_probe=probe
    )
    assert resolved == _IBS_NANKAI_GATEWAY_URL  # not dropped
    assert "probe_skip_reason" not in metadata  # not dead-flagged
    # A real 502 → deferred (probe_defer_reason); a probe timeout → NOT deferred
    # (kept pending, fetched this run). The observed verdict decides which:
    if metadata.get("probe_timeout"):
        assert "probe_defer_reason" not in metadata  # timeout does not block fetch
    else:
        assert metadata.get("probe_defer_reason") is not None  # 502 still deferred


# Regression (real HTTP): https://cz.nankai.edu.cn/zj2_16415/main.htm returns
# 200 OK — it is a LIVE faculty-list page. The browser console's 404s there are
# for JS-dynamically-loaded SUB-RESOURCES (images/css), NOT the page URL itself.
# A page that loads (200) but has broken sub-resources is still a valid page to
# crawl: the extractor reads DOM text, not missing images. So the probe MUST
# classify this URL as `ok` and入图前 keep it pending (not dropped/deferred/skipped).
# No mocks: real aiohttp; skipped when the network is unavailable.
_CZ_NANKAI_LIVE_PAGE_URL = "https://cz.nankai.edu.cn/zj2_16415/main.htm"


async def test_probe_cz_nankai_live_page_is_ok_not_dead(live_http):
    verdict = await StatusProbe().probe(_CZ_NANKAI_LIVE_PAGE_URL)
    assert verdict.category == OK
    assert verdict.status_code == 200


async def test_resolve_discovered_url_cz_nankai_live_page_stays_pending(live_http):
    """入图前 integration: a page that loads 200 (even with broken sub-resources
    logged in the browser console) must stay pending for crawling — not dropped,
    not skipped (dead), not deferred (5xx/unreachable).
    """
    from dext.engine.seeds import resolve_discovered_url

    probe = StatusProbe()
    resolved, metadata = await resolve_discovered_url(
        _CZ_NANKAI_LIVE_PAGE_URL, status_probe=probe
    )
    assert resolved == _CZ_NANKAI_LIVE_PAGE_URL  # not dropped
    assert "probe_skip_reason" not in metadata   # not dead (404/410)
    assert "probe_defer_reason" not in metadata   # not 5xx/unreachable
    assert metadata["source_url"] == _CZ_NANKAI_LIVE_PAGE_URL
