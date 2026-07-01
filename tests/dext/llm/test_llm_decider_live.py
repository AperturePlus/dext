from dext.llm.decider import DeciderContext, DeciderNode, decide_links
from dext.page.links import LinkSignal, PageSnapshot

_LABELS = {"college", "faculty_list", "pagination", "followup", "reslice", "detail", "noise", "login"}


def _sig(url, text, path):
    return LinkSignal(href=url, url=url, anchor_text=text, heading="师资队伍",
                      parent_class="teacher-list", path_segments=path, same_site=True)


async def test_decide_links_shape_and_labels(llm_client):
    cands = [
        _sig("https://x.edu.cn/teacher/info/1001", "张三 教授", ["teacher", "info", "1001"]),
        _sig("https://x.edu.cn/login", "登录", ["login"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/szdw.htm", final_url="https://x.edu.cn/szdw.htm",
        title="数学学院 师资队伍", text_snapshot="本院教师名单：张三 教授 ……",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="数学学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)

    assert decision.parse_error is None
    allowed_urls = {c.url for c in cands}
    for link in decision.links:
        assert link.url in allowed_urls  # anti-hallucination held
        assert link.label in _LABELS
    assert isinstance(decision.page_is_leaf, bool)


async def test_decide_links_excludes_retired_keeps_international(llm_client):
    cands = [
        _sig("https://x.edu.cn/jrxy/retired.htm", "离退休教师", ["jrxy", "retired.htm"]),
        _sig("https://x.edu.cn/sis/szdw.htm", "国际关系学院 师资队伍", ["sis", "szdw.htm"]),
        _sig("https://x.edu.cn/math/teacher/1001.htm", "张三 教授", ["math", "teacher", "1001.htm"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/szdw.htm", final_url="https://x.edu.cn/szdw.htm",
        title="师资队伍", text_snapshot="栏目：离退休教师；国际关系学院师资；教师 张三 教授。",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="某学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    by_url = {l.url: l for l in decision.links}
    actionable = {"college", "faculty_list", "pagination", "followup", "detail"}

    retired = by_url.get("https://x.edu.cn/jrxy/retired.htm")
    if retired is not None:                       # 离退休 → 轴 B 排除
        assert retired.label not in actionable
    sis = by_url.get("https://x.edu.cn/sis/szdw.htm")
    if sis is not None:                           # 国际关系学院 → 反误伤，不排除
        assert sis.exclusion_reason is None


async def test_decide_page_exclusion_for_arts_college(llm_client):
    cands = [_sig("https://x.edu.cn/art/teacher/1.htm", "王五", ["art", "teacher", "1.htm"])]
    snap = PageSnapshot(
        url="https://x.edu.cn/art/szdw.htm", final_url="https://x.edu.cn/art/szdw.htm",
        title="艺术学院 师资队伍",
        text_snapshot="艺术学院下设美术系、音乐系、设计系。本院教师名单如下：王五 副教授……",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="艺术学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    assert decision.page_exclusion_reason == "arts"


async def test_decide_labels_title_reslice_and_keeps_dept_followup(llm_client):
    cands = [
        _sig("https://x.edu.cn/szdw/jiaoshou/index.html", "教授", ["szdw", "jiaoshou"]),
        _sig("https://x.edu.cn/szdw/fujiaoshou/index.html", "副教授", ["szdw", "fujiaoshou"]),
        _sig("https://x.edu.cn/szdw/zhexuexi/index.html", "哲学系", ["szdw", "zhexuexi"]),
        _sig("https://x.edu.cn/szdw/jianzhi/index.html", "兼职教授", ["szdw", "jianzhi"]),
        _sig("https://x.edu.cn/teacher/1001.htm", "张三 教授", ["teacher", "1001"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/szdw.htm", final_url="https://x.edu.cn/szdw.htm",
        title="师资队伍",
        text_snapshot="师资队伍。按职称查看：教授、副教授。按系查看：哲学系。兼职教授。教师：张三 教授。",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="某学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    for link in decision.links:
        assert link.label in _LABELS
    by_url = {l.url: l for l in decision.links}

    jiaoshou = by_url.get("https://x.edu.cn/szdw/jiaoshou/index.html")
    if jiaoshou is not None:                        # 职称 → 同群体 re-slice
        assert jiaoshou.label == "reslice"
        assert jiaoshou.facet_axis == "title"
    dept = by_url.get("https://x.edu.cn/szdw/zhexuexi/index.html")
    if dept is not None:                            # 系 → 组织子单元，绝不是 reslice
        assert dept.label != "reslice"
    jianzhi = by_url.get("https://x.edu.cn/szdw/jianzhi/index.html")
    if jianzhi is not None:                         # 兼职 → 异群体，不塌缩为 reslice
        assert jianzhi.label != "reslice"
