from pathlib import Path

import pytest
from pydantic import ValidationError

from dext.seed import (
    Manifest,
    OrgUnitSeed,
    SeedError,
    UniversitySeed,
    get_university,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_SEED = REPO_ROOT / "entrances.yaml"


def test_university_with_listing_urls_is_valid():
    u = UniversitySeed(
        name="北京航空航天大学",
        url="https://www.buaa.edu.cn/",
        org_unit_listing_urls=["https://www.buaa.edu.cn/jgsz/jxkyjg.htm"],
    )
    assert u.org_unit_listing_urls


def test_university_with_org_units_faculty_urls_is_valid():
    u = UniversitySeed(
        name="西安交通大学",
        url="https://www.xjtu.edu.cn/",
        org_units=[
            OrgUnitSeed(
                name="数学学院",
                kind="college",
                faculty_urls=["https://math.xjtu.edu.cn/szdw/jsml.htm"],
            )
        ],
    )
    assert u.org_units[0].faculty_urls


def test_university_with_org_unit_url_is_valid():
    u = UniversitySeed(
        name="某大学",
        url="https://x.edu.cn/",
        org_units=[OrgUnitSeed(name="某学院", url="https://col.x.edu.cn/")],
    )
    assert u.org_units[0].url


def test_university_without_any_entry_point_raises():
    with pytest.raises(ValidationError):
        UniversitySeed(name="空大学", url="https://empty.edu.cn/")


def test_org_unit_kind_defaults_to_college():
    assert OrgUnitSeed(name="某学院").kind == "college"


def test_load_real_manifest_succeeds():
    m = load_manifest(REAL_SEED)
    assert m.version == 1
    assert len(m.universities) == 38  # update if entrances.yaml grows
    names = {u.name for u in m.universities}
    assert "北京航空航天大学" in names


def test_load_manifest_uses_settings_default_path(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    from dext.config import get_settings

    get_settings.cache_clear()
    m = load_manifest()  # path=None -> settings.seed_path == Path("entrances.yaml")
    assert len(m.universities) == 38
    get_settings.cache_clear()


def test_load_manifest_missing_file_raises_seed_error(tmp_path):
    with pytest.raises(SeedError):
        load_manifest(tmp_path / "nope.yaml")


def test_load_manifest_invalid_entry_raises_seed_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nuniversities:\n  - name: 空\n    url: https://x.edu.cn/\n",
        encoding="utf-8",
    )
    with pytest.raises(SeedError):
        load_manifest(bad)


def test_get_university_hit():
    m = load_manifest(REAL_SEED)
    u = get_university(m, "北京航空航天大学")
    assert u.url == "https://www.buaa.edu.cn/"


def test_get_university_miss_lists_name():
    m = load_manifest(REAL_SEED)
    with pytest.raises(SeedError) as exc:
        get_university(m, "不存在大学")
    assert "不存在大学" in str(exc.value)
