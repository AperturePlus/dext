from dext.bridge.redirect import (
    BLOCKED, OFFSITE_OK, OK, PROBE_FAILED, RedirectGuard, classify_redirect,
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


async def test_probe_failure_does_not_block():
    async def resolver(url):
        raise RuntimeError("WAF")
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")).verdict == PROBE_FAILED
