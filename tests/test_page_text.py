import hashlib

from dext.page.text import content_hash, html_to_text


def test_html_to_text_extracts_visible_chinese_text():
    out = html_to_text("<html><body><h1>数学学院</h1><p>张三 教授</p></body></html>")
    assert "数学学院" in out
    assert "张三 教授" in out


def test_html_to_text_drops_images_and_link_urls_keeps_anchor_text():
    out = html_to_text('<p><img src="a.png" alt="ALT"><a href="https://x/p">李四</a></p>')
    assert "李四" in out          # anchor text kept
    assert "https://x/p" not in out  # link URL dropped (ignore_links)
    assert "a.png" not in out     # image dropped (ignore_images)


def test_html_to_text_no_hard_wrapping():
    long_line = "教授" * 80
    out = html_to_text(f"<p>{long_line}</p>")
    # body_width=0 → the paragraph is not folded into multiple hard-wrapped lines.
    assert long_line in out.replace("\n", "")
    assert out.count("\n") < 5


def test_html_to_text_handles_empty():
    assert html_to_text("") == ""


def test_content_hash_matches_sha256_utf8():
    html = "<html>张三</html>"
    assert content_hash(html) == hashlib.sha256(html.encode("utf-8")).hexdigest()


def test_content_hash_is_deterministic_and_sensitive():
    assert content_hash("<a>x</a>") == content_hash("<a>x</a>")
    assert content_hash("<a>x</a>") != content_hash("<a>y</a>")
    assert len(content_hash("anything")) == 64
