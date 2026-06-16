from dext.page.candidates import (
    FilterContext,
    FilterResult,
    filter_detail_candidates,
    filter_navigation_candidates,
)
from dext.exclusions import (
    BASIC_EDUCATION_CENTER,
    EXPERIMENT_CENTER,
    classify_excluded_org_unit,
    classify_excluded_page_link,
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
    assert set(res.dropped) == {"excluded", "external", "duplicate", "already_enriched"}


def test_drop_scu_confirmed_excluded_faculty_pages():
    sigs = [
        _sig("https://sesu.scu.edu.cn/szdw/ltxjs.htm", "离退休教师"),
        _sig("https://sesu.scu.edu.cn/szdw/bshldz.htm", "博士后流动站"),
        _sig("https://lj.scu.edu.cn/szzr/ltxjzg.htm", "离退休教职工"),
        _sig("https://x.edu.cn/szdw/zhangsan.htm", "张三 教授"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx(same_site_only=False))
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/zhangsan.htm"]
    assert res.dropped["excluded"] == 3


def test_drop_explicit_staff_and_admin_exclusion_links():
    sigs = [
        _sig("https://x.edu.cn/szdw/rsgz.htm", "人事工作"),
        _sig("https://x.edu.cn/szdw/jjzx.htm", "基教中心"),
        _sig("https://x.edu.cn/szdw/syzx.htm", "实验中心"),
        _sig("https://x.edu.cn/szdw/jfg.htm", "教辅岗"),
        _sig("https://x.edu.cn/szdw/xzg.htm", "行政岗"),
        _sig("https://x.edu.cn/szdw/zzxz.htm", "专职行政"),
        _sig("https://x.edu.cn/szdw/xzry.htm", "行政人员"),
        _sig("https://x.edu.cn/szdw/xztd.htm", "行政团队"),
        _sig("https://x.edu.cn/szdw/xz.htm", "行政"),
        _sig("https://x.edu.cn/szdw/zhangsan.htm", "张三"),
    ]
    res = filter_navigation_candidates(_snap(sigs), _ctx())
    assert [s.anchor_text for s in res.kept] == ["张三"]
    assert res.dropped["excluded"] == 9


def test_org_unit_excludes_basic_education_and_experiment_centers():
    assert classify_excluded_org_unit("基教中心") == BASIC_EDUCATION_CENTER
    assert classify_excluded_org_unit("基础教学中心") == BASIC_EDUCATION_CENTER
    assert classify_excluded_org_unit("实验中心") == EXPERIMENT_CENTER


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
    assert res.dropped["excluded"] == 0
    assert classify_excluded_page_link(title="行政法教师团队") is None
    assert classify_excluded_page_link(heading="行政管理系教师") is None


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
