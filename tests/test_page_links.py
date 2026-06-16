from dext.page.links import LinkSignal, PageSnapshot, build_snapshot

_HTML = """
<html><head><title>数学学院 师资</title></head><body>
  <h2>教授</h2>
  <div class="teacher-list">
    <a href="/szdw/zhangsan.htm">张三</a>
    <a href="https://math.x.edu.cn/szdw/lisi.htm">李四</a>
    <a href="/szdw/zhangsan.htm">张三(重复)</a>
    <a href="javascript:void(0)">下一页</a>
    <a href="https://other.com/x">外部</a>
  </div>
</body></html>
"""


def _snap():
    return build_snapshot(
        _HTML,
        requested_url="https://math.x.edu.cn/szdw/index.htm",
        final_url="https://math.x.edu.cn/szdw/index.htm",
        title="",
    )


def test_snapshot_basic_shape():
    snap = _snap()
    assert isinstance(snap, PageSnapshot)
    assert snap.title == "数学学院 师资"  # from <title> when title arg empty
    assert "张三" in snap.text_snapshot
    assert len(snap.content_hash) == 64


def test_links_are_normalized_absolute_and_deduped():
    snap = _snap()
    # javascript: dropped; /szdw/zhangsan.htm appears once; absolute + relative resolved.
    assert "https://math.x.edu.cn/szdw/zhangsan.htm" in snap.links
    assert "https://math.x.edu.cn/szdw/lisi.htm" in snap.links
    assert "https://other.com/x" in snap.links
    assert snap.links.count("https://math.x.edu.cn/szdw/zhangsan.htm") == 1
    assert all("javascript" not in u for u in snap.links)


def test_link_signals_keep_every_anchor_including_duplicate_urls():
    snap = _snap()
    # 5 anchors, javascript: one dropped (normalize → None) → 4 signals (dup URL kept).
    assert len(snap.link_signals) == 4
    zhang = [s for s in snap.link_signals if s.url.endswith("zhangsan.htm")]
    assert len(zhang) == 2  # both anchors to the same URL retained as separate signals


def test_link_signal_fields():
    snap = _snap()
    lisi = next(s for s in snap.link_signals if s.url.endswith("lisi.htm"))
    assert lisi.anchor_text == "李四"
    assert lisi.heading == "教授"                 # nearest preceding heading
    assert lisi.parent_class == "teacher-list"     # nearest ancestor with a class
    assert lisi.path_segments == ["szdw", "lisi.htm"]
    assert lisi.same_site is True                  # same host as final_url


def test_offsite_signal_marked_not_same_site():
    snap = _snap()
    ext = next(s for s in snap.link_signals if s.url == "https://other.com/x")
    assert ext.same_site is False


def test_explicit_port_anchor_is_dropped():
    html = """
    <html><body>
      <a href="https://ce.lzu.edu.cn:8080/staff/static/show_szdw.html?userId=abc">刘木</a>
      <a href="/staff/static/show_szdw.html?userId=ok">正常</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://ce.lzu.edu.cn/list.htm", "https://ce.lzu.edu.cn/list.htm", "")
    assert snap.links == ["https://ce.lzu.edu.cn/staff/static/show_szdw.html?userId=ok"]
    assert [s.anchor_text for s in snap.link_signals] == ["正常"]


def test_build_snapshot_handles_empty_html():
    snap = build_snapshot("", "https://x.edu.cn/", "https://x.edu.cn/", "")
    assert snap.links == []
    assert snap.link_signals == []
    assert snap.title == ""
