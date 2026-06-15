"""DeepSeek V4 client (spec §2). Stateless, shareable across concurrent workers.

Single model (deepseek-v4-flash); thinking + reasoning_effort toggles replace the
old chat/reasoner split. Single-shot only — reasoning_content is never fed back
into message history (V4 multi-turn rule).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)  # [{"name", "arguments": dict}]
    invalid_tool_calls: list[dict] = field(default_factory=list)  # [{"name","arguments_raw","error"}]
    usage: dict | None = None
    reasoning_content: str | None = None


class LLMClient:
    def __init__(self, settings):
        self._settings = settings
        self._client = None  # lazy AsyncOpenAI

    @property
    def settings(self):
        return self._settings

    def _ensure(self):
        if self._client is None:
            if not self._settings.deepseek_api_key:
                raise RuntimeError(
                    "DEEPSEEK_API_KEY is not set; cannot call the LLM. Set it in .env."
                )
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self._settings.llm_base_url,
                api_key=self._settings.deepseek_api_key,
            )
        return self._client

    def _build_kwargs(self, messages, *, tools, tool_choice, response_format, thinking, retry_mode) -> dict:
        kwargs: dict = {
            "model": self._settings.llm_model,
            "messages": messages,
            "stream": False,
            "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format
        if thinking:
            kwargs["reasoning_effort"] = (
                self._settings.llm_reasoning_effort_retry
                if retry_mode
                else self._settings.llm_reasoning_effort
            )
        return kwargs

    async def chat(self, messages, *, tools=None, tool_choice=None, response_format=None,
                   thinking: bool = True, retry_mode: bool = False) -> LLMResponse:
        client = self._ensure()
        kwargs = self._build_kwargs(
            messages, tools=tools, tool_choice=tool_choice,
            response_format=response_format, thinking=thinking, retry_mode=retry_mode,
        )
        resp = await client.chat.completions.create(**kwargs)
        return self._parse(resp)

    @staticmethod
    def _parse(resp) -> LLMResponse:
        msg = resp.choices[0].message
        tool_calls: list[dict] = []
        invalid: list[dict] = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            name = tc.function.name
            raw = tc.function.arguments
            try:
                tool_calls.append({"name": name, "arguments": json.loads(raw)})
            except (json.JSONDecodeError, TypeError) as exc:
                invalid.append({"name": name, "arguments_raw": raw, "error": str(exc)})
        usage = None
        if getattr(resp, "usage", None) is not None:
            usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            invalid_tool_calls=invalid,
            usage=usage,
            reasoning_content=getattr(msg, "reasoning_content", None),
        )
