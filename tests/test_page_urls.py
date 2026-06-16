from dext.page.urls import is_offsite, normalize_url, same_site


def test_relative_resolves_to_absolute():
    assert normalize_url("szdw/2.htm", "https://x.edu.cn/teacher/index.htm") == \
        "https://x.edu.cn/teacher/szdw/2.htm"


def test_fragment_is_stripped():
    assert normalize_url("page.htm#top", "https://x.edu.cn/") == "https://x.edu.cn/page.htm"


def test_host_lowercased_without_explicit_port():
    assert normalize_url("HTTP://X.EDU.CN/a", "https://x.edu.cn/") == "http://x.edu.cn/a"


def test_explicit_ports_are_rejected_even_defaults():
    assert normalize_url("https://x.edu.cn:8443/a", "https://x.edu.cn/") is None
    assert normalize_url("https://x.edu.cn:443/a", "https://x.edu.cn/") is None
    assert normalize_url("http://x.edu.cn:80/a", "https://x.edu.cn/") is None
    assert normalize_url("/a", "https://x.edu.cn:443/") is None


def test_trailing_path_slash_stripped_but_root_kept():
    assert normalize_url("https://x.edu.cn/szdw/", "https://x.edu.cn/") == "https://x.edu.cn/szdw"
    assert normalize_url("https://x.edu.cn/", "https://x.edu.cn/") == "https://x.edu.cn/"


def test_query_preserved_verbatim_no_reordering():
    # KISS + protects the form-pagination synthetic-URL byte-match: no param sort.
    u = "https://x.edu.cn/list.jsp?py=a&page=2"
    assert normalize_url(u, u) == u


def test_ycl_synthetic_params_preserved_and_idempotent():
    syn = "https://x.edu.cn/xylb.jsp?py=a&__ycl_kind=form&__ycl_form=fromWen&__ycl_field=fromWenNOWPAGE&__ycl_page=2"
    assert normalize_url(syn, syn) == syn  # untouched → node_key/cache key stays stable


def test_non_http_schemes_return_none():
    assert normalize_url("javascript:void(0)", "https://x.edu.cn/") is None
    assert normalize_url("mailto:a@x.edu.cn", "https://x.edu.cn/") is None
    assert normalize_url("tel:123", "https://x.edu.cn/") is None


def test_empty_or_none_href_returns_none():
    assert normalize_url("", "https://x.edu.cn/") is None
    assert normalize_url("   ", "https://x.edu.cn/") is None
    assert normalize_url(None, "https://x.edu.cn/") is None


def test_same_site_exact_host():
    assert same_site("https://x.edu.cn/a", "https://x.edu.cn/b")
    assert not same_site("https://a.x.edu.cn/a", "https://b.x.edu.cn/a")
    assert not same_site("https://x.edu.cn:443/a", "https://x.edu.cn/b")


def test_same_site_loose_registrable_domain():
    # subdomains of the same university collapse under loose matching (edu.cn aware).
    assert same_site("https://math.xjtu.edu.cn/p", "https://www.xjtu.edu.cn/q", loose=True)
    assert not same_site("https://math.xjtu.edu.cn/p", "https://www.pku.edu.cn/q", loose=True)


def test_is_offsite_uses_loose_registrable_domain():
    assert not is_offsite("https://math.xjtu.edu.cn/p", "https://www.xjtu.edu.cn/list.htm")
    assert is_offsite("https://third-party.com/p", "https://www.xjtu.edu.cn/list.htm")
    assert is_offsite("https://math.xjtu.edu.cn:443/p", "https://www.xjtu.edu.cn/list.htm")
