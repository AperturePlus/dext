"""Constrained LLM adapter for Topic extraction and existing-ID selection."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from dext_graph.catalog.topics import TopicConcept, validate_concepts
from dext_graph.config import GraphSettings
from dext_graph.models import ValueValidationError


class TopicLLMClient:
    def __init__(self, settings: GraphSettings, *, client: Any | None = None) -> None:
        if client is None and not settings.topic_llm_api_key:
            raise ValueValidationError("DEEPSEEK_API_KEY is not set for Topic linking")
        self.settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.topic_llm_api_key,
            base_url=settings.topic_llm_base_url,
            timeout=settings.topic_llm_timeout_seconds,
            max_retries=settings.topic_llm_max_retries,
        )
        self._owns_client = client is None
        self.last_rejected_count = 0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def extract(self, raw_text: str) -> list[TopicConcept]:
        response = await self._client.chat.completions.create(
            model=self.settings.topic_llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract only concepts explicitly present in the supplied research "
                        "statement. Return one JSON object with a concepts array. Each item "
                        "must contain evidence_span copied verbatim, canonical_name, kind "
                        "(discipline|method|task|application_domain|research_object), and "
                        "relation_type (PRIMARY_TOPIC|USES_METHOD|APPLIED_TO|TARGETS_TASK|STUDIES). "
                        "Do not infer unstated concepts."
                    ),
                },
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
        content = response.choices[0].message.content or "{}"
        try:
            body = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueValidationError("Topic extractor returned invalid JSON") from exc
        values = body.get("concepts") if isinstance(body, dict) else None
        if not isinstance(values, list):
            raise ValueValidationError("Topic extractor response lacks concepts array")
        objects = [item for item in values if isinstance(item, dict)]
        validated = validate_concepts(raw_text, objects)
        self.last_rejected_count = len(values) - len(validated)
        return validated

    async def select(
        self, concept: TopicConcept, candidates: list[dict[str, Any]]
    ) -> tuple[str, float] | str:
        allowed = {str(candidate["id"]): float(candidate["score"]) for candidate in candidates}
        response = await self._client.chat.completions.create(
            model=self.settings.topic_llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Choose an existing Topic only when it is the same concept, not merely "
                        "related or broader/narrower. Return JSON with selected_topic_id set to "
                        "one supplied ID, or null and new_topic=true."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "concept": {
                                "evidence_span": concept.evidence_span,
                                "canonical_name": concept.canonical_name,
                                "kind": concept.kind,
                                "relation_type": concept.relation_type,
                            },
                            "candidates": candidates,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
        try:
            body = json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError as exc:
            raise ValueValidationError("Topic selector returned invalid JSON") from exc
        selected = body.get("selected_topic_id") if isinstance(body, dict) else None
        if selected is None and isinstance(body, dict) and body.get("new_topic") is True:
            return "new_topic"
        selected_id = str(selected or "")
        if selected_id not in allowed:
            raise ValueValidationError("Topic selector chose an ID outside the candidate set")
        return selected_id, allowed[selected_id]


__all__ = ["TopicLLMClient"]
