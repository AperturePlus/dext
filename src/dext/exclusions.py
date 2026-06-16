"""High-precision exclusion hints shared by page and engine layers.

These are deliberately narrow. Broader semantic calls belong in the LLM prompts:
when a signal is ambiguous, keep it and let the model decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

EXCLUSION_AXIS_ORG = "A"   # 整个机构（学院/中心）
EXCLUSION_AXIS_UNIT = "B"  # 正常学院内部的人员/页面子类


@dataclass(frozen=True)
class ExclusionCategory:
    code: str
    zh: str
    axis: str


EXCLUSION_CATEGORIES: tuple[ExclusionCategory, ...] = (
    ExclusionCategory("sino_foreign_joint", "中外合作办学 / 联合办学（按办学性质，不因“国际”二字）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("arts", "艺术学院（艺术 / 美术 / 音乐 / 设计类）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("sports", "体育学院 / 体育部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("continuing_education", "成人教育 / 继续教育 / 网络教育学院", EXCLUSION_AXIS_ORG),
    ExclusionCategory("basic_education_center", "基础教学中心 / 基教中心 / 公共课教学部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("experiment_center", "实验中心 / 实验教学中心", EXCLUSION_AXIS_ORG),
    ExclusionCategory("under_construction", "筹建学院（学院（筹）/ 筹建 / 筹备）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("excellence_engineer", "卓越工程师学院（专项培养，通常无独立师资名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("academy", "书院（住宿制 / 通识，通常无独立教师名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("postdoc", "博士后流动站 / 博士后工作站", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("retired", "离退休教师 / 离退休教职工", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("administration", "专职行政 / 行政岗 / 行政人员 / 行政团队 / 管理岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("support_role", "教辅岗 / 实验技术岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("personnel_work", "人事工作栏目（非教师）", EXCLUSION_AXIS_UNIT),
)

_VALID_CODES: frozenset[str] = frozenset(c.code for c in EXCLUSION_CATEGORIES)


def is_valid_exclusion_reason(code: str | None) -> bool:
    """LLM 回的 exclusion_reason 是否落在词表内（解析时校验，非法归 None）。"""
    return bool(code) and code in _VALID_CODES


def render_exclusion_policy() -> str:
    """生成注入三份提示词的同一段排除策略文字；不含任何具体学校的专有名/域名/路径。"""
    axis_a = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG
    )
    axis_b = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT
    )
    return (
        "排除策略（宁可漏排除，不要错排除）：信号不明确时一律保留给后续爬取，不要因泛词误伤。\n"
        "轴 A——当前**整个机构（学院/中心）**属下列类别时排除：\n"
        f"{axis_a}\n"
        "轴 B——正常学院内部的下列**人员/页面子类**排除（学院本身保留）：\n"
        f"{axis_b}\n"
        "反误伤：不因“国际 / 工程 / 工程师”等泛词排除正常招生学院（如国际关系学院）；"
        "不因“行政法 / 行政管理”等学科、研究方向或普通履历文字排除正常教师。"
    )


SINO_FOREIGN_JOINT = "sino_foreign_joint"
ARTS = "arts"
SPORTS = "sports"
POSTDOC = "postdoc"
RETIRED = "retired"
CONTINUING_EDUCATION = "continuing_education"
PERSONNEL_WORK = "personnel_work"
BASIC_EDUCATION_CENTER = "basic_education_center"
EXPERIMENT_CENTER = "experiment_center"
SUPPORT_ROLE = "support_role"
ADMINISTRATION = "administration"

_WS = re.compile(r"\s+")

_EXACT_ORG_NAMES = {
    "匹兹堡学院": SINO_FOREIGN_JOINT,
    "艺术学院": ARTS,
    "体育学院": SPORTS,
    "成人教育学院": CONTINUING_EDUCATION,
    "继续教育学院": CONTINUING_EDUCATION,
    "成人继续教育学院": CONTINUING_EDUCATION,
    "基教中心": BASIC_EDUCATION_CENTER,
    "基础教学中心": BASIC_EDUCATION_CENTER,
    "实验中心": EXPERIMENT_CENTER,
}

_EXACT_HOSTS = {
    "scupi.scu.edu.cn": SINO_FOREIGN_JOINT,
}

_EXACT_HOST_PATHS = {
    ("sesu.scu.edu.cn", "/szdw/ltxjs.htm"): RETIRED,
    ("sesu.scu.edu.cn", "/szdw/bshldz.htm"): POSTDOC,
    ("lj.scu.edu.cn", "/szzr/ltxjzg.htm"): RETIRED,
}

_EXACT_TEXT_MARKERS = (
    (RETIRED, ("离退休教师", "离退休教职工")),
    (POSTDOC, ("博士后流动站", "博士后工作站")),
    (CONTINUING_EDUCATION, ("成人教育学院", "继续教育学院", "成人继续教育学院")),
    (ARTS, ("艺术学院",)),
    (SPORTS, ("体育学院",)),
    (PERSONNEL_WORK, ("人事工作",)),
    (BASIC_EDUCATION_CENTER, ("基教中心", "基础教学中心")),
    (EXPERIMENT_CENTER, ("实验中心",)),
    (SUPPORT_ROLE, ("教辅岗",)),
    (ADMINISTRATION, ("行政岗", "专职行政", "行政人员", "行政团队")),
)

_EXACT_TEXT_VALUES = (
    (ADMINISTRATION, ("行政",)),
)


def _compact(text: str | None) -> str:
    return _WS.sub("", text or "")


def classify_excluded_org_unit(name: str | None, *, url: str | None = None) -> str | None:
    """Return an exclusion reason for an org unit only on exact, high-confidence hits."""
    compact = _compact(name)
    if compact in _EXACT_ORG_NAMES:
        return _EXACT_ORG_NAMES[compact]
    if url:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return _EXACT_HOSTS.get(host)
    return None


def classify_excluded_page_link(
    *,
    url: str | None = None,
    anchor_text: str | None = None,
    title: str | None = None,
    heading: str | None = None,
    org_unit_name: str | None = None,
) -> str | None:
    """Classify obvious excluded page/link signals without fuzzy token matching."""
    org_reason = classify_excluded_org_unit(org_unit_name, url=url)
    if org_reason is not None:
        return org_reason

    if url:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        if host in _EXACT_HOSTS:
            return _EXACT_HOSTS[host]
        if (host, path) in _EXACT_HOST_PATHS:
            return _EXACT_HOST_PATHS[(host, path)]

    compact_parts = tuple(_compact(part) for part in (anchor_text, title, heading) if part)
    for reason, values in _EXACT_TEXT_VALUES:
        if any(part in values for part in compact_parts):
            return reason

    haystack = "".join(compact_parts)
    if not haystack:
        return None
    for reason, markers in _EXACT_TEXT_MARKERS:
        if any(marker in haystack for marker in markers):
            return reason
    return None
