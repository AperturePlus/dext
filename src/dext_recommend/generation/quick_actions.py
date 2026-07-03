"""LLM-backed quick-action label generation for chat composer chips."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from dext_grounded import ConstrainedGenerationPipeline, FactBundle
from dext_recommend.core.generation_profile import RecommendGenerationProfile

_MAX_RECAPS = 5
_MAX_ACTIONS = 4
_MAX_LABEL_CHARS = 8
_QUESTION_PREFIXES = (
    "你",
    "是否",
    "请问",
    "能否",
    "可以",
    "如何",
    "怎么",
    "为什么",
    "哪位",
    "哪些",
)
_QUESTION_SUFFIXES = ("吗", "么", "呢")


class QuickActionGenerationService:
    """Generate short UI action labels from the current follow-up context.

    This service intentionally returns a plain list. The API contract treats
    quick actions as optional UI affordances; any generation/validation failure
    degrades to an empty list so the client hides the chips.
    """

    def __init__(
        self,
        *,
        pipeline: ConstrainedGenerationPipeline,
        generation_profile: RecommendGenerationProfile,
    ) -> None:
        self._pipeline = pipeline
        self._generation_profile = generation_profile

    async def generate(
        self,
        follow_up: str,
        last_recommendations: Sequence[Mapping[str, Any]] | None = None,
    ) -> list[str]:
        op = self._generation_profile.operations["quick_actions"]
        try:
            result = await asyncio.wait_for(
                self._pipeline.generate(
                    system_prompt_id=op.system_prompt_id,
                    user_inputs={
                        "follow_up": str(follow_up),
                        "last_recommendations": _normalize_recaps(last_recommendations),
                        "locale": "zh-CN",
                        "surface": "chat_composer_quick_actions",
                        "label_constraints": {
                            "min_items": 1,
                            "max_items": _MAX_ACTIONS,
                            "max_cjk_chars": _MAX_LABEL_CHARS,
                            "style": "operation phrases only; no full questions",
                            "forbidden_prefixes": ["你", "是否", "请问"],
                        },
                    },
                    fact_bundle=FactBundle(
                        build_id="quick-actions",
                        subject_id="quick_actions",
                        facts=(),
                        source_refs=(),
                    ),
                    student_context=None,
                    json_schema=dict(op.json_schema),
                    generation_profile_version=self._generation_profile.version,
                    safety_domain="recommend",
                    include_contacts=False,
                    operation_id="quick_actions",
                    subject_kind="mentor",
                ),
                timeout=op.timeout,
            )
        except Exception:
            return []
        if result.warnings:
            return []
        output = result.output if isinstance(result.output, dict) else {}
        actions = output.get("quick_actions")
        if not isinstance(actions, list):
            return []
        return _sanitize_actions(actions)


def _normalize_recaps(
    last_recommendations: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    recaps: list[dict[str, Any]] = []
    for item in tuple(last_recommendations or ())[:_MAX_RECAPS]:
        if not isinstance(item, Mapping):
            continue
        raw_fields = item.get("research_fields") or ()
        if isinstance(raw_fields, (str, bytes)) or not isinstance(raw_fields, Sequence):
            raw_fields = ()
        recaps.append({
            "professor_id": _string_or_none(item.get("professor_id")),
            "name": _string_or_none(item.get("name")),
            "university": _string_or_none(item.get("university")),
            "research_fields": [
                str(value).strip()
                for value in raw_fields
                if isinstance(value, str) and value.strip()
            ],
        })
    return recaps


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _sanitize_actions(actions: Sequence[Any]) -> list[str]:
    sanitized: list[str] = []
    seen: set[str] = set()
    for raw in actions:
        if not isinstance(raw, str):
            continue
        label = raw.strip()
        if not _is_valid_label(label) or label in seen:
            continue
        sanitized.append(label)
        seen.add(label)
        if len(sanitized) >= _MAX_ACTIONS:
            break
    return sanitized


def _is_valid_label(label: str) -> bool:
    if not label or len(label) > _MAX_LABEL_CHARS:
        return False
    if "?" in label or "？" in label:
        return False
    if label.startswith(_QUESTION_PREFIXES):
        return False
    if label.endswith(_QUESTION_SUFFIXES):
        return False
    return True


__all__ = ["QuickActionGenerationService"]
