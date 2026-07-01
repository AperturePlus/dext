from dext.page.links import build_snapshot
from dext.page.pagination import (
    FollowupCandidate,
    PaginationCandidate,
    find_followup_links,
    find_url_pagination,
)

_LIST_URL = "https://x.edu.cn/szdw/index.htm"
_PAGINATION_HTML = """
<html><body><div class="pager">
  <a href="/szdw/2.htm">2</a>
  <a href="/szdw/index_3.html">3</a>
  <a href="list.htm?page=2">下一页</a>
  <a href="https://x.edu.cn/news/100.htm">新闻</a>
  <a href="https://other.edu.cn/szdw/2.htm">别校</a>
</div></body></html>
"""


def _snap(html, url=_LIST_URL):
    return build_snapshot(html, url, url, "")


def test_find_url_pagination_recognizes_numeric_and_query_pages():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    by_url = {c.url: c for c in cands}
    assert "https://x.edu.cn/szdw/2.htm" in by_url
    assert by_url["https://x.edu.cn/szdw/2.htm"].page_index == 2
    assert "https://x.edu.cn/szdw/index_3.html" in by_url
    assert by_url["https://x.edu.cn/szdw/index_3.html"].page_index == 3
    assert "https://x.edu.cn/szdw/list.htm?page=2" in by_url
    assert by_url["https://x.edu.cn/szdw/list.htm?page=2"].page_index == 2


def test_find_url_pagination_excludes_other_columns_and_other_sites():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    urls = {c.url for c in cands}
    assert "https://x.edu.cn/news/100.htm" not in urls       # different column
    assert "https://other.edu.cn/szdw/2.htm" not in urls     # different site


def test_find_url_pagination_returns_pagination_candidate_type():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    assert cands and all(isinstance(c, PaginationCandidate) for c in cands)


_FOLLOWUP_HTML = """
<html><body><ul class="cat">
  <a href="/szdw/jiaoshou.htm">教授</a>
  <a href="/szdw/fujiaoshou.htm">副教授</a>
  <a href="/szdw/bodao.htm">博士生导师</a>
  <a href="/szdw/index.htm">师资队伍</a>
  <a href="/news/1.htm">学院新闻</a>
</ul></body></html>
"""


def test_find_followup_links_recognizes_categories():
    cands = find_followup_links(_snap(_FOLLOWUP_HTML), _LIST_URL)
    labels = {c.label for c in cands}
    assert "教授" in labels
    assert "副教授" in labels
    assert "博士生导师" in labels
    assert all(isinstance(c, FollowupCandidate) for c in cands)


def test_find_followup_links_skips_news_and_the_list_itself():
    cands = find_followup_links(_snap(_FOLLOWUP_HTML), _LIST_URL)
    urls = {c.url for c in cands}
    assert "https://x.edu.cn/news/1.htm" not in urls           # not a category
    assert "https://x.edu.cn/szdw/index.htm" not in urls        # the list page itself


def test_find_followup_links_honors_limit():
    anchors = "".join(f'<a href="/szdw/c{i}.htm">教授{i}</a>' for i in range(50))
    snap = _snap(f"<html><body>{anchors}</body></html>")
    cands = find_followup_links(snap, _LIST_URL, limit=36)
    assert len(cands) == 36
