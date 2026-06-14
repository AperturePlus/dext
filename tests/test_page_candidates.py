from dext.page.candidates import FilterContext, FilterResult, filter_detail_candidates
from dext.page.links import LinkSignal, PageSnapshot

_LIST_URL = "https://x.edu.cn/szdw/index.htm"


def _sig(url, text="老师", same=True):
    return LinkSignal(
        href=url, url=url, anchor_text=text, heading=None,
        parent_class=None, path_segments=url.split("/")[3:], same_site=same,
    )


def _snap(signals):
    return PageSnapshot(
        url=_LIST_URL, final_url=_LIST_URL, title="", text_snapshot="",
        links=[s.url for s in signals], link_signals=signals, content_hash="h",
    )


def _ctx(**kw):
    return FilterContext(faculty_list_url=_LIST_URL, **kw)


def test_keeps_plain_detail_link():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/zhangsan.htm", "张三")]), _ctx())
    assert isinstance(res, FilterResult)
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/zhangsan.htm"]
    assert sum(res.dropped.values()) == 0


def test_drop_external():
    res = filter_detail_candidates(_snap([_sig("https://other.com/p", "张三", same=False)]), _ctx())
    assert res.kept == []
    assert res.dropped["external"] == 1


def test_drop_retired():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/lao.htm", "张三（退休）")]), _ctx())
    assert res.dropped["retired"] == 1


def test_drop_noise_by_text_and_path():
    sigs = [
        _sig("https://x.edu.cn/szdw/a.htm", "登录"),
        _sig("https://x.edu.cn/search/q.htm", "查询"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx())
    assert res.dropped["noise"] == 2


def test_drop_directory():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/all.htm", "教师名单")]), _ctx())
    assert res.dropped["directory"] == 1


def test_drop_unrelated_path():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/xygk/intro.htm", "学院简介")]), _ctx())
    assert res.dropped["unrelated_path"] == 1


def test_drop_duplicate_within_page():
    sigs = [
        _sig("https://x.edu.cn/szdw/z.htm", "张三"),
        _sig("https://x.edu.cn/szdw/z.htm", "张三链接2"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx())
    assert len(res.kept) == 1
    assert res.dropped["duplicate"] == 1


def test_drop_already_enriched():
    sig = _sig("https://x.edu.cn/szdw/z.htm", "张三")
    res = filter_detail_candidates(_snap([sig]), _ctx(already_enriched={"https://x.edu.cn/szdw/z.htm"}))
    assert res.kept == []
    assert res.dropped["already_enriched"] == 1


def test_dropped_dict_always_has_all_reason_codes():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/z.htm", "张三")]), _ctx())
    assert set(res.dropped) == {
        "noise", "directory", "retired", "external",
        "unrelated_path", "duplicate", "already_enriched",
    }
