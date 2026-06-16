"""High-precision exclusion hints shared by page and engine layers.

These are deliberately narrow. Broader semantic calls belong in the LLM prompts:
when a signal is ambiguous, keep it and let the model decide.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

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
