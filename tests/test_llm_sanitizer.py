from dext.llm.sanitizer import sanitize


def test_returns_none_when_no_name():
    assert sanitize({"title": "教授"}) is None
    assert sanitize({"name": "   "}) is None


def test_name_trimmed_and_inner_whitespace_collapsed():
    p = sanitize({"name": "  张   三 "})
    assert p is not None
    assert p.name == "张 三"


def test_multivalue_list_joined_deduped_with_chinese_semicolon():
    p = sanitize({"name": "李四", "research_areas": ["AI", "AI", "  ML  ", ""]})
    assert p.research_areas == "AI；ML"


def test_multivalue_string_split_on_common_separators():
    p = sanitize({"name": "李四", "publications": "论文A；论文B，论文C、论文A"})
    assert p.publications == "论文A；论文B；论文C"


def test_invalid_email_dropped_valid_kept():
    assert sanitize({"name": "王五", "email": "not-an-email"}).email is None
    assert sanitize({"name": "王五", "email": " a@b.edu.cn "}).email == "a@b.edu.cn"


def test_non_http_urls_dropped():
    p = sanitize({"name": "王五", "homepage": "ftp://x/y", "external_link": "https://e.com/p"})
    assert p.homepage is None
    assert p.external_link == "https://e.com/p"


def test_unknown_fields_left_empty_not_fabricated():
    p = sanitize({"name": "赵六"})
    assert p.title is None and p.bio is None and p.phone is None
    assert p.research_areas is None and p.publications is None
