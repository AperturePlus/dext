"""LLM-backed title generation for chat conversation sessions."""
from __future__ import annotations

import asyncio
from typing import Any

from dext_grounded import ConstrainedGenerationPipeline, FactBundle
from dext_recommend.core.generation_profile import RecommendGenerationProfile

MAX_TITLE_CHARS = 32
_FALLBACK_TITLE = "新会话"


class ConversationTitleGenerationService:
    """Generate a short display title for the first completed chat turn.

    Title generation is best-effort UI metadata. Any model, validation, or
    transport failure degrades to a deterministic first-message title.
    """

    def __init__(
        self,
        *,
        pipeline: ConstrainedGenerationPipeline,
        generation_profile: RecommendGenerationProfile,
    ) -> None:
        self._pipeline = pipeline
        self._generation_profile = generation_profile

    async def generate(self, first_user_message: str, assistant_answer: str = "") -> str:
        fallback = fallback_title(first_user_message)
        try:
            op = self._generation_profile.operations["conversation_title"]
            result = await asyncio.wait_for(
                self._pipeline.generate(
                    system_prompt_id=op.system_prompt_id,
                    user_inputs={
                        "first_user_message": _normalize_text(first_user_message)[:4096],
                        "assistant_answer": _normalize_text(assistant_answer)[:2048],
                        "locale": "zh-CN",
                        "title_constraints": {
                            "max_chars": MAX_TITLE_CHARS,
                            "style": "short noun phrase, no question mark, no markdown",
                        },
                    },
                    fact_bundle=FactBundle(
                        build_id="conversation-title",
                        subject_id="conversation_title",
                        facts=(),
                        source_refs=(),
                    ),
                    student_context=None,
                    json_schema=dict(op.json_schema),
                    generation_profile_version=self._generation_profile.version,
                    safety_domain="recommend",
                    include_contacts=False,
                    operation_id="conversation_title",
                    subject_kind="mentor",
                ),
                timeout=op.timeout,
            )
        except Exception:
            return fallback
        if result.warnings:
            return fallback
        output = result.output if isinstance(result.output, dict) else {}
        title = sanitize_title(output.get("title"))
        return title or fallback


def fallback_title(first_user_message: str) -> str:
    return sanitize_title(first_user_message) or _FALLBACK_TITLE


def sanitize_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _normalize_text(value)[:MAX_TITLE_CHARS]


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().split())


__all__ = [
    "ConversationTitleGenerationService",
    "MAX_TITLE_CHARS",
    "fallback_title",
    "sanitize_title",
]
