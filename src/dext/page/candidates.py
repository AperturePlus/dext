"""Detail-candidate pre-filter (spec §6, source doc §8.4). Cheap, deterministic,
diagnosable: every drop has a reason code and a count. The real "is this a
teacher detail page" judgement is the LLM decider's (SP5); this layer only removes
obvious non-candidates and produces counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dext.page.links import LinkSignal, PageSnapshot
from dext.page.urls import is_offsite

_DROP_CODES = (
    "noise", "directory", "retired", "external",
    "unrelated_path", "duplicate", "already_enriched",
)

_RETIRED_TEXT = ("退休", "荣休", "离任", "名誉", "已故", "去世", "逝世")
_NOISE_TEXT = (
    "登录", "登陆", "搜索", "查询", "下载", "新闻", "通知", "公告", "首页",
    "返回", "上一页", "下一页", "更多", "联系我们", "版权", "管理", "english",
)
_NOISE_URL_TOKENS = ("/login", "/logout", "/search", "/download", "/rss")
_DIRECTORY_TEXT = ("师资队伍", "教师名单", "导师名单", "全部教师", "教师一览", "师资力量")

# People-ish path tokens that protect a same-site link from unrelated_path.
_PEOPLE_TOKENS = (
    "szdw", "teacher", "faculty", "jsdw", "jsml", "rcjs", "professor",
    "people", "master", "doctor", "导师", "教师", "师资", "教授",
)
# Common non-people site sections (top path segment) for unrelated_path.
_NONPEOPLE_SECTIONS = (
    "news", "xwzx", "tzgg", "notice", "xygk", "gk", "kxyj", "kycg",
    "research", "download", "lxwm", "contact", "zsjy", "jyxx", "xshd",
)


@dataclass
class FilterContext:
    faculty_list_url: str
    already_enriched: set[str] = field(default_factory=set)
    same_site_only: bool = True


@dataclass
class FilterResult:
    kept: list[LinkSignal]
    dropped: dict[str, int]


def _is_noise(sig: LinkSignal) -> bool:
    text = (sig.anchor_text or "").lower()
    if any(kw.lower() in text for kw in _NOISE_TEXT):
        return True
    low_url = sig.url.lower()
    return any(token in low_url for token in _NOISE_URL_TOKENS)


def _is_unrelated_path(url: str) -> bool:
    low_url = url.lower()
    if any(token in low_url for token in _PEOPLE_TOKENS):
        return False
    segments = [seg for seg in urlsplit(url).path.split("/") if seg]
    if not segments:
        return False
    return segments[0].lower() in _NONPEOPLE_SECTIONS


def filter_detail_candidates(snapshot: PageSnapshot, context: FilterContext) -> FilterResult:
    dropped = {code: 0 for code in _DROP_CODES}
    kept: list[LinkSignal] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        url = sig.url
        if url in context.already_enriched:
            dropped["already_enriched"] += 1
            continue
        if url in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(url)
        if context.same_site_only and is_offsite(url, context.faculty_list_url):
            dropped["external"] += 1
            continue
        if any(kw in (sig.anchor_text or "") for kw in _RETIRED_TEXT):
            dropped["retired"] += 1
            continue
        if _is_noise(sig):
            dropped["noise"] += 1
            continue
        if any(kw in (sig.anchor_text or "") for kw in _DIRECTORY_TEXT):
            dropped["directory"] += 1
            continue
        if _is_unrelated_path(url):
            dropped["unrelated_path"] += 1
            continue
        kept.append(sig)
    return FilterResult(kept=kept, dropped=dropped)
