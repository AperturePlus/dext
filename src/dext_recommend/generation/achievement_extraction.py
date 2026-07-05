"""LLM-backed extraction of user-entered profile achievements."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from dext_grounded import ConstrainedGenerationPipeline, FactBundle
from dext_recommend.core.generation_profile import RecommendGenerationProfile

ResearchType = Literal["paper", "project", "patent", "other"]

_MAX_RAW_TEXT_CHARS = 4096
_PART_SPLIT_RE = re.compile(r"[；;、。.\r\n]+")
_YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年?")
_CCF_RE = re.compile(r"CCF[-\s]?[ABC]", re.IGNORECASE)
_AWARDS = (
    "特等奖",
    "一等奖",
    "二等奖",
    "三等奖",
    "金牌",
    "银牌",
    "铜牌",
    "优秀奖",
    "获奖",
)
_COMPETITION_SIGNALS = (
    "竞赛",
    "比赛",
    "大赛",
    "挑战赛",
    "区域赛",
    "蓝桥杯",
    "数学建模",
)
_COMPETITION_ABBR_SIGNALS = ("ACM", "ICPC", "CCPC")
_GENERIC_RESEARCH_TITLES = {
    "科研经历",
    "论文经历",
    "科研成果",
    "研究经历",
    "项目经历",
    "专利经历",
    "论文",
    "项目",
    "课题",
    "专利",
}
_RESEARCH_TYPES = {"paper", "project", "patent", "other"}


class AchievementExtractionService:
    """Extract competitions and research items from free-form user text.

    The model is the primary extractor. Post-processing enforces the public HTTP
    contract and removes generic placeholders; a deterministic fallback preserves
    obvious achievements if generation is unavailable or incomplete.
    """

    def __init__(
        self,
        *,
        pipeline: ConstrainedGenerationPipeline,
        generation_profile: RecommendGenerationProfile,
    ) -> None:
        self._pipeline = pipeline
        self._generation_profile = generation_profile

    async def extract(self, raw_text: str) -> dict[str, list[dict[str, str]]]:
        text = _normalize_text(raw_text)[:_MAX_RAW_TEXT_CHARS]
        if not text:
            return empty_achievement_draft()
        fallback = fallback_achievement_draft(text)
        try:
            op = self._generation_profile.operations["achievement_extraction"]
            result = await asyncio.wait_for(
                self._pipeline.generate(
                    system_prompt_id=op.system_prompt_id,
                    user_inputs={
                        "raw_text": text,
                        "locale": "zh-CN",
                        "contract": {
                            "competitions": ["name", "level", "award", "year"],
                            "research": [
                                "type",
                                "title",
                                "role",
                                "venue_or_status",
                                "year",
                            ],
                            "allowed_research_types": sorted(_RESEARCH_TYPES),
                            "unknown_fields": "empty string",
                        },
                    },
                    fact_bundle=FactBundle(
                        build_id="achievement-extraction",
                        subject_id="achievement_extraction",
                        facts=(),
                        source_refs=(),
                    ),
                    student_context=None,
                    json_schema=dict(op.json_schema),
                    generation_profile_version=self._generation_profile.version,
                    safety_domain="recommend",
                    include_contacts=False,
                    operation_id="achievement_extraction",
                    subject_kind="student_profile",
                ),
                timeout=op.timeout,
            )
        except Exception:
            return fallback
        if result.warnings:
            return fallback
        extracted = sanitize_achievement_draft(result.output)
        return _merge_with_fallback(extracted, fallback)


def empty_achievement_draft() -> dict[str, list[dict[str, str]]]:
    return {"competitions": [], "research": []}


def fallback_achievement_draft(raw_text: str) -> dict[str, list[dict[str, str]]]:
    competitions: list[dict[str, str]] = []
    research: list[dict[str, str]] = []
    for part in _split_parts(raw_text):
        if _looks_like_competition(part):
            item = _fallback_competition(part)
            if item is not None:
                competitions.append(item)
        research_item = _fallback_research(part)
        if research_item is not None:
            research.append(research_item)
    return {"competitions": competitions, "research": research}


def sanitize_achievement_draft(raw: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(raw, Mapping):
        return empty_achievement_draft()
    return {
        "competitions": _sanitize_competitions(raw.get("competitions")),
        "research": _sanitize_research(raw.get("research")),
    }


def _sanitize_competitions(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return out
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = _string(item.get("name"))
        if not name:
            continue
        out.append({
            "name": name,
            "level": _string(item.get("level")),
            "award": _string(item.get("award")),
            "year": _string(item.get("year")),
        })
    return out


def _sanitize_research(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return out
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        kind = _string(item.get("type"))
        if kind not in _RESEARCH_TYPES:
            kind = "other"
        title = _string(item.get("title"))
        if not title or title in _GENERIC_RESEARCH_TITLES:
            continue
        out.append({
            "type": kind,
            "title": title,
            "role": _string(item.get("role")),
            "venue_or_status": _string(item.get("venue_or_status")),
            "year": _string(item.get("year")),
        })
    return out


def _merge_with_fallback(
    extracted: dict[str, list[dict[str, str]]],
    fallback: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    return {
        "competitions": _dedupe_items(
            extracted["competitions"] or fallback["competitions"],
            key_fields=("name", "award", "year"),
        ),
        "research": _dedupe_items(
            extracted["research"] or fallback["research"],
            key_fields=("type", "title", "year"),
        ),
    }


def _dedupe_items(
    items: Sequence[dict[str, str]],
    *,
    key_fields: Sequence[str],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        key = tuple(item.get(field, "") for field in key_fields)
        if key in seen:
            continue
        out.append(item)
        seen.add(key)
    return out


def _split_parts(raw_text: str) -> list[str]:
    return [part.strip() for part in _PART_SPLIT_RE.split(raw_text) if part.strip()]


def _looks_like_competition(text: str) -> bool:
    upper = text.upper()
    return any(signal in text for signal in _COMPETITION_SIGNALS) or any(
        signal in upper for signal in _COMPETITION_ABBR_SIGNALS
    )


def _fallback_competition(text: str) -> dict[str, str] | None:
    award = _extract_award(text)
    year = _extract_year(text)
    level = _extract_level(text)
    name = _clean_competition_name(text, award=award)
    if not name:
        return None
    return {"name": name, "level": level, "award": award, "year": year}


def _extract_award(text: str) -> str:
    return next((award for award in _AWARDS if award in text), "")


def _extract_year(text: str) -> str:
    match = _YEAR_RE.search(text)
    return match.group(1) if match else ""


def _extract_level(text: str) -> str:
    if "国际" in text:
        return "国际级"
    if "国家级" in text or "国家" in text or "国赛" in text:
        return "国家级"
    if "省级" in text or "省赛" in text:
        return "省级"
    if "校级" in text or "校赛" in text:
        return "校级"
    return ""


def _clean_competition_name(text: str, *, award: str) -> str:
    cleaned = text.strip()
    cleaned = _YEAR_RE.sub("", cleaned)
    if award:
        cleaned = cleaned.replace(award, "")
    for token in (
        "我参加过",
        "参加过",
        "参加",
        "获得",
        "荣获",
        "取得",
        "拿到",
        "获",
        "国际级",
        "国家级",
        "省级",
        "校级",
    ):
        cleaned = cleaned.replace(token, "")
    return _trim_text(cleaned)


def _fallback_research(text: str) -> dict[str, str] | None:
    kind = _research_type(text)
    if kind is None:
        return None
    year = _extract_year(text)
    title = _clean_research_title(text)
    if not title or title in _GENERIC_RESEARCH_TITLES:
        return None
    return {
        "type": kind,
        "title": title,
        "role": "",
        "venue_or_status": "",
        "year": year,
    }


def _research_type(text: str) -> ResearchType | None:
    upper = text.upper()
    if "论文" in text or "SCI" in upper or "EI" in upper or _CCF_RE.search(text):
        return "paper"
    if "专利" in text:
        return "patent"
    if "项目" in text or "课题" in text:
        return "project"
    if "科研" in text or "研究" in text:
        return "other"
    return None


def _clean_research_title(text: str) -> str:
    cleaned = text.strip()
    cleaned = _YEAR_RE.sub("", cleaned)
    changed = True
    while changed:
        before = cleaned
        cleaned = re.sub(r"^(我)?(发表|录用|撰写|参与|完成|获得|有|做了|做过)了?", "", cleaned)
        cleaned = re.sub(r"^(一|二|两|三|四|五|六|七|八|九|十|\d+)\s*篇", "", cleaned)
        cleaned = re.sub(r"^(一|二|两|三|四|五|六|七|八|九|十|\d+)\s*项", "", cleaned)
        cleaned = cleaned.strip()
        changed = cleaned != before
    return _trim_text(cleaned)


def _string(value: Any) -> str:
    return _normalize_text(value) if isinstance(value, str) else ""


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _trim_text(text: str) -> str:
    return text.strip(" \t，,：:；;。.-")


__all__ = [
    "AchievementExtractionService",
    "empty_achievement_draft",
    "fallback_achievement_draft",
    "sanitize_achievement_draft",
]
