"""C4 QA generation schemas and shared constants."""
from __future__ import annotations

from typing import Any

QA_GENERATION_PROFILE_VERSION = "competition.qa.v1"
ANSWER_SYSTEM_PROMPT_ID = "dext_competition.qa.answer.v1"
COMPARE_SYSTEM_PROMPT_ID = "dext_competition.qa.compare.v1"
SAFETY_DOMAIN = "competition"

FRESHNESS_NOTICE = (
    "报名时间、赛道、费用、AI 使用规则和本校认定均可能按届次变化；"
    "请以当届官网、主办单位、省级赛区/承办高校和本校教务处文件复核。"
)

FRESHNESS_KEYWORDS = (
    "报名",
    "时间",
    "日期",
    "赛道",
    "费用",
    "缴费",
    "AI",
    "人工智能",
    "资格",
    "校内",
    "认定",
    "综测",
    "加分",
    "经费",
    "本校",
    "往届",
    "去年",
    "上一届",
    "2024",
)

ANSWER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "claims"],
    "properties": {
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "content_class", "fact_indices"],
                "properties": {
                    "text": {"type": "string"},
                    "content_class": {
                        "type": "string",
                        "enum": ["fact", "advice", "uncertain"],
                    },
                    "fact_indices": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
            },
        },
    },
}

COMPARE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "claims"],
    "properties": {
        "summary": {"type": "string"},
        "claims": ANSWER_JSON_SCHEMA["properties"]["claims"],
    },
}


def is_freshness_sensitive(text: str | None) -> bool:
    """Return whether text asks about fields that require current-year review."""

    value = text or ""
    return any(keyword in value for keyword in FRESHNESS_KEYWORDS)


__all__ = [
    "ANSWER_JSON_SCHEMA",
    "ANSWER_SYSTEM_PROMPT_ID",
    "COMPARE_JSON_SCHEMA",
    "COMPARE_SYSTEM_PROMPT_ID",
    "FRESHNESS_NOTICE",
    "QA_GENERATION_PROFILE_VERSION",
    "SAFETY_DOMAIN",
    "is_freshness_sensitive",
]

