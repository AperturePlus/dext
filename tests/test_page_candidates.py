from dext.page.candidates import (
    FilterContext,
    FilterResult,
    filter_detail_candidates,
    filter_navigation_candidates,
)
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


def test_keeps_ambiguous_retired_text_for_llm():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/lao.htm", "张三（退休）")]), _ctx())
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/lao.htm"]


def test_keeps_ambiguous_noise_for_llm():
    sigs = [
        _sig("https://x.edu.cn/szdw/a.htm", "登录"),
        _sig("https://x.edu.cn/search/q.htm", "查询"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx())
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/a.htm", "https://x.edu.cn/search/q.htm"]


def test_keeps_directory_for_llm():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/all.htm", "教师名单")]), _ctx())
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/all.htm"]


def test_keeps_unrelated_path_for_llm():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/xygk/intro.htm", "学院简介")]), _ctx())
    assert [s.url for s in res.kept] == ["https://x.edu.cn/xygk/intro.htm"]


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
    assert set(res.dropped) == {"external", "duplicate", "already_enriched"}


def test_keeps_admin_law_and_admin_management_teacher_signals():
    sigs = [
        _sig("https://x.edu.cn/szdw/xzfx.htm", "行政法教师团队"),
        _sig("https://x.edu.cn/szdw/xzglt.htm", "行政管理系教师"),
        _sig("https://x.edu.cn/szdw/zhangsan.htm", "张三 行政职务：系主任"),
        _sig("https://x.edu.cn/szdw/lisi.htm", "李四 行政管理研究方向"),
    ]
    res = filter_navigation_candidates(_snap(sigs), _ctx())
    assert [s.anchor_text for s in res.kept] == [
        "行政法教师团队",
        "行政管理系教师",
        "张三 行政职务：系主任",
        "李四 行政管理研究方向",
    ]


def test_navigation_candidates_keep_scu_law_title_categories_for_llm():
    sigs = [
        _sig("https://law.scu.edu.cn/szdw/zzjzg_link/fgzc.htm", "副高职称"),
        _sig("https://law.scu.edu.cn/szdw/zzjzg_link/zjzc.htm", "中级职称"),
        _sig("https://law.scu.edu.cn/info/1360/15754.htm", "左卫民"),
    ]
    snap = PageSnapshot(
        url="https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        final_url="https://law.scu.edu.cn/szdw/zzjzg_link/zgzc.htm",
        title="正高职称-四川大学法学院",
        text_snapshot="",
        links=[s.url for s in sigs],
        link_signals=sigs,
        content_hash="h",
    )
    res = filter_navigation_candidates(
        snap,
        FilterContext(faculty_list_url=snap.url),
    )
    assert [s.url for s in res.kept] == [
        "https://law.scu.edu.cn/szdw/zzjzg_link/fgzc.htm",
        "https://law.scu.edu.cn/szdw/zzjzg_link/zjzc.htm",
        "https://law.scu.edu.cn/info/1360/15754.htm",
    ]


def test_semantic_exclusion_terms_are_kept_for_llm_not_prefiltered():
    sigs = [
        _sig("https://x.edu.cn/djgz/index.htm", "党建工作"),
        _sig("https://x.edu.cn/xsgz/index.htm", "学工队伍"),
        _sig("https://x.edu.cn/cwgl/index.htm", "财务管理"),
        _sig("https://x.edu.cn/jz/index.htm", "学术讲座"),
        _sig("https://x.edu.cn/bsh/index.htm", "博士后"),
        _sig("https://x.edu.cn/kzjs/index.htm", "客座教授"),
        _sig("https://x.edu.cn/hyds/index.htm", "行业导师"),
        _sig("https://x.edu.cn/wjjs/index.htm", "外籍教师"),
        _sig("https://x.edu.cn/jzds/index.htm", "兼职导师"),
    ]

    res = filter_navigation_candidates(_snap(sigs), _ctx())

    assert [s.url for s in res.kept] == [s.url for s in sigs]
    assert sum(res.dropped.values()) == 0
