from dext.bridge.redirect import (
    BLOCKED, OFFSITE_OK, OK, PROBE_FAILED, PROBE_TIMEOUT, RedirectGuard, classify_redirect,
)


def test_classify_blocks_wechat():
    v = classify_redirect("https://teacher.uni.edu.cn/p", "https://mp.weixin.qq.com/s/abc")
    assert v.verdict == BLOCKED and v.reason == "wechat_redirect"
    assert v.final_host == "mp.weixin.qq.com"


def test_classify_offsite_personal_homepage_ok():
    v = classify_redirect("https://cs.uni.edu.cn/faculty/zhang", "https://zhang.github.io/")
    assert v.verdict == OFFSITE_OK and v.final_host == "zhang.github.io"


def test_classify_same_host_ok():
    v = classify_redirect("https://cs.uni.edu.cn/a", "https://cs.uni.edu.cn/a/index.html")
    assert v.verdict == OK


async def test_probe_blocks_wechat_via_resolver():
    async def resolver(url):
        return "https://mp.weixin.qq.com/s/x"
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://t.uni.edu.cn/p")).verdict == BLOCKED


async def test_probe_offsite_ok_via_resolver():
    async def resolver(url):
        return "https://zhang.github.io/"
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://cs.uni.edu.cn/f/zhang")).verdict == OFFSITE_OK


async def test_probe_failure_returns_probe_failed():
    async def resolver(url):
        raise RuntimeError("WAF")
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")).verdict == PROBE_FAILED


async def test_probe_timeout_returns_probe_timeout_not_probe_failed():
    # Redirect-probe timeout = the off-channel probe couldn't resolve redirects in time.
    # The browser may still load the URL, so a timeout is a non-blocking "no signal"
    # verdict, distinct from probe_failed (loop/DNS/WAF). Neither drops the URL; the
    # timeout verdict just carries a more accurate reason code for diagnostics.
    async def resolver(url):
        raise TimeoutError("total timeout")

    verdict = await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")
    assert verdict.verdict == PROBE_TIMEOUT
    assert verdict.verdict != PROBE_FAILED
    assert verdict.reason == "probe_timeout"


async def test_probe_timeout_logs_compact_exception_summary(caplog):
    async def resolver(url):
        raise TimeoutError("total timeout")

    with caplog.at_level("INFO", logger="dext.bridge.redirect"):
        verdict = await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")

    assert verdict.verdict == PROBE_TIMEOUT
    assert "redirect probe timeout url=https://x/p error=TimeoutError" in caplog.text


async def test_probe_failure_logs_compact_exception_summary(caplog):
    async def resolver(url):
        raise RuntimeError("WAF")

    with caplog.at_level("INFO", logger="dext.bridge.redirect"):
        verdict = await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")

    assert verdict.verdict == PROBE_FAILED
    assert "redirect probe failed url=https://x/p error=RuntimeError message=WAF" in caplog.text


async def test_probe_too_many_redirects_log_omits_verbose_history(caplog):
    class FakeTooManyRedirects(Exception):
        history = ["<ClientResponse(...) [302 None]>\n<CIMultiDictProxy(...)>"] * 10

    FakeTooManyRedirects.__name__ = "TooManyRedirects"

    async def resolver(url):
        raise FakeTooManyRedirects("verbose redirect history")

    with caplog.at_level("INFO", logger="dext.bridge.redirect"):
        verdict = await RedirectGuard(resolver=resolver).probe_redirect("https://sirpa.fudan.edu.cn/_redirect")

    assert verdict.verdict == PROBE_FAILED
    assert "error=TooManyRedirects redirects=10" in caplog.text
    assert "ClientResponse" not in caplog.text
    assert "CIMultiDictProxy" not in caplog.text
