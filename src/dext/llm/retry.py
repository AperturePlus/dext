"""no_structured_data recoverability classifier (spec §6.2, source doc §12.4).

Pure function: an extractor that returns nothing on a *rich* detail page is
recoverable (retry); on a sparse/irrelevant page it is terminal (fail).
SP5 only classifies; SP6 owns the state transition and failure logging.
"""

from __future__ import annotations

from dataclasses import dataclass

from dext.page.links import PageSnapshot

_MIN_TEXT_LEN = 200
_HOMEPAGE_URL_TOKENS = (
    "teacher", "szdw", "info", "faculty", "people",
    "professor", "jsdw", "rcjs", "/show", "content",
)
_RICH_TOKENS = (
    "个人简介", "教育经历", "科研项目", "论文著作",
    "研究方向", "工作经历", "学习经历", "研究成果",
)
_TITLE_TOKENS = ("教授", "研究员", "院士", "副教授", "讲师", "助理教授")


@dataclass
class NoDataVerdict:
    recoverable: bool
    reason: str
    last_error: str | None = None


def assess_no_data(snapshot: PageSnapshot) -> NoDataVerdict:
    text = snapshot.text_snapshot or ""
    url = (snapshot.url or "").lower()
    long_enough = len(text) >= _MIN_TEXT_LEN
    homepage_like = any(tok in url for tok in _HOMEPAGE_URL_TOKENS)
    has_rich = any(tok in text for tok in _RICH_TOKENS)
    has_title = any(tok in text for tok in _TITLE_TOKENS)
    if long_enough and homepage_like and has_rich and has_title:
        return NoDataVerdict(
            recoverable=True,
            reason="rich detail page with no structured records",
            last_error="rich_detail_no_structured_data",
        )
    missing = []
    if not long_enough:
        missing.append("text_too_short")
    if not homepage_like:
        missing.append("url_not_homepage_like")
    if not has_rich:
        missing.append("no_rich_tokens")
    if not has_title:
        missing.append("no_title_tokens")
    return NoDataVerdict(recoverable=False, reason="not a rich detail page: " + ",".join(missing))
