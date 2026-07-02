from dext_recommend.facts.source_urls import canonicalize_source_url, dedupe_source_urls


def test_canonicalize_strips_utm_and_fragment():
    raw = "https://x.edu/p?utm_source=foo&id=1#frag"
    assert canonicalize_source_url(raw) == "https://x.edu/p?id=1"


def test_canonicalize_lowercases_scheme_host():
    assert canonicalize_source_url("HTTPS://X.edu/P") == "https://x.edu/P"


def test_canonicalize_none_or_empty():
    assert canonicalize_source_url(None) is None
    assert canonicalize_source_url("") is None


def test_canonicalize_strips_tracking_params_only():
    raw = "https://x.edu/p?fbclid=abc&keep=1&utm_medium=email"
    got = canonicalize_source_url(raw)
    assert "fbclid" not in got and "utm_medium" not in got
    assert "keep=1" in got


def test_dedupe_source_urls_stable_and_canonical():
    urls = [
        "https://x.edu/p?utm_source=foo",
        "https://x.edu/p",
        None,
        "https://y.edu/q",
        "https://x.edu/p#frag",
    ]
    assert dedupe_source_urls(urls) == ("https://x.edu/p", "https://y.edu/q")
