def test_page_package_imports():
    import dext.page  # noqa: F401


def test_beautifulsoup_available():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<a href='x'>t</a>", "html.parser")
    assert soup.find("a").get("href") == "x"
