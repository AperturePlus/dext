from dext.llm.decider import DeciderContext, DeciderNode, decide_links
from dext.page.links import LinkSignal, PageSnapshot

_LABELS = {"college", "faculty_list", "pagination", "followup", "detail", "noise", "login"}


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


async def test_decide_links_exclusion_smoke(llm_client):
    cands = [
        _sig("https://scupi.scu.edu.cn/", "匹兹堡学院", []),
        _sig("https://sesu.scu.edu.cn/szdw/ltxjs.htm", "离退休教师", ["szdw", "ltxjs.htm"]),
        _sig("https://sesu.scu.edu.cn/szdw/bshldz.htm", "博士后流动站", ["szdw", "bshldz.htm"]),
        _sig("https://lj.scu.edu.cn/szzr/ltxjzg.htm", "离退休教职工", ["szzr", "ltxjzg.htm"]),
        _sig("https://x.edu.cn/art/szdw.htm", "艺术学院 师资队伍", ["art", "szdw.htm"]),
        _sig("https://x.edu.cn/tyxy/szdw.htm", "体育学院 教师名单", ["tyxy", "szdw.htm"]),
        _sig("https://x.edu.cn/jxjy/szdw.htm", "继续教育学院 师资队伍", ["jxjy", "szdw.htm"]),
        _sig("https://x.edu.cn/math/teacher/1001.htm", "张三 教授", ["math", "teacher", "1001.htm"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/schools.htm",
        final_url="https://x.edu.cn/schools.htm",
        title="四川大学 院系与师资入口",
        text_snapshot=(
            "候选包含匹兹堡学院 SCUPI、离退休教师 ltxjs、博士后流动站 bshldz、"
            "离退休教职工 ltxjzg、艺术学院、体育学院、继续教育学院，以及普通教师张三。"
        ),
        links=[c.url for c in cands],
        link_signals=cands,
        content_hash="h",
    )
    node = DeciderNode(type="org_listing_url", url=snap.url, depth=0, org_unit_name=None)
    ctx = DeciderContext(university_name="四川大学", visited_summary="", faculty_list_url="")

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)

    assert decision.parse_error is None
    by_url = {link.url: link for link in decision.links}
    excluded = {
        "https://scupi.scu.edu.cn/",
        "https://sesu.scu.edu.cn/szdw/ltxjs.htm",
        "https://sesu.scu.edu.cn/szdw/bshldz.htm",
        "https://lj.scu.edu.cn/szzr/ltxjzg.htm",
        "https://x.edu.cn/art/szdw.htm",
        "https://x.edu.cn/tyxy/szdw.htm",
        "https://x.edu.cn/jxjy/szdw.htm",
    }
    actionable = {"college", "faculty_list", "pagination", "followup", "detail"}
    for url in excluded:
        if url in by_url:
            assert by_url[url].label not in actionable
            assert by_url[url].is_leaf is False
