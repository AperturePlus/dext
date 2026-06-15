from dext.llm.retry import NoDataVerdict, assess_no_data
from dext.page.links import PageSnapshot


def _snap(text: str, url: str = "https://x.edu.cn/teacher/info/1001") -> PageSnapshot:
    return PageSnapshot(
        url=url, final_url=url, title="t", text_snapshot=text,
        links=[], link_signals=[], content_hash="h",
    )


_RICH = "个人简介：张三，教授，博士生导师。教育经历……研究方向：人工智能。" * 8


def test_rich_detail_is_recoverable():
    v = assess_no_data(_snap(_RICH))
    assert isinstance(v, NoDataVerdict)
    assert v.recoverable is True
    assert v.last_error == "rich_detail_no_structured_data"


def test_sparse_page_is_terminal():
    v = assess_no_data(_snap("欢迎访问本站。"))
    assert v.recoverable is False
    assert v.last_error is None


def test_rich_tokens_but_non_homepage_url_is_terminal():
    v = assess_no_data(_snap(_RICH, url="https://x.edu.cn/news/list.htm"))
    assert v.recoverable is False


def test_homepage_url_but_no_title_token_is_terminal():
    text = ("个人简介。教育经历。研究方向。" * 10)  # rich tokens, no 教授/研究员/院士
    v = assess_no_data(_snap(text))
    assert v.recoverable is False
