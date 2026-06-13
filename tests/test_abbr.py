import re
from pathlib import Path

from dext.seed import (
    UniversitySeed,
    db_filename,
    load_manifest,
    resolve_abbr,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_SEED = REPO_ROOT / "entrances.yaml"


def _uni(url: str, abbr: str | None = None) -> UniversitySeed:
    return UniversitySeed(
        name="x", url=url, abbr=abbr, org_unit_listing_urls=["https://x.edu.cn/x.htm"]
    )


def test_resolve_abbr_from_hostname():
    assert resolve_abbr(_uni("https://www.buaa.edu.cn/")) == "buaa"


def test_resolve_abbr_strips_www_and_public_suffix():
    cases = {
        "https://www.pku.edu.cn/": "pku",
        "https://www.tsinghua.edu.cn/": "tsinghua",
        "https://www.dlut.edu.cn/": "dlut",
        "https://www.sjtu.edu.cn/": "sjtu",
        "https://www.sysu.edu.cn/": "sysu",
    }
    for url, expected in cases.items():
        assert resolve_abbr(_uni(url)) == expected


def test_explicit_abbr_overrides_hostname_and_is_slugified():
    assert resolve_abbr(_uni("https://www.buaa.edu.cn/", abbr="  BUAA-X ")) == "buaa-x"


def test_all_seed_abbrs_are_unique_nonempty_and_slug_clean():
    m = load_manifest(REAL_SEED)
    abbrs = [resolve_abbr(u) for u in m.universities]
    assert all(a for a in abbrs)  # non-empty
    assert all(re.fullmatch(r"[a-z0-9-]+", a) for a in abbrs)  # slug-clean
    assert len(abbrs) == 38
    assert len(set(abbrs)) == 38  # zero collisions


def test_db_filename():
    assert db_filename("buaa") == "buaa.db"
