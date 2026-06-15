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
