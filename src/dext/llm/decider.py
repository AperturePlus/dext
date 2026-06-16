"""Navigation decider (spec §3, source doc §7.1).

Labels pre-filtered candidate links and flags potential leaf pages. JSON-object
structured output. Anti-hallucination: only URLs present in the input candidate
set survive. The decider labels only — it never fetches, navigates, or self-retries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from dext.exclusions import is_valid_exclusion_reason
from dext.llm.client import LLMClient
from dext.llm.prompts import build_decider_messages
from dext.page.links import LinkSignal, PageSnapshot

_LABELS = {"college", "faculty_list", "pagination", "followup", "reslice", "detail", "noise", "login"}
_RESLICE_AXES = {"title", "letter", "advisor"}


@dataclass
class DeciderNode:
    type: str
    url: str
    depth: int = 0
    org_unit_name: str | None = None


@dataclass
class DeciderContext:
    university_name: str = ""
    visited_summary: str = ""
    faculty_list_url: str = ""


@dataclass
class DecidedLink:
    url: str
    label: str
    confidence: float
    is_leaf: bool
    org_unit_name: str | None = None
    exclusion_reason: str | None = None
    facet_axis: str | None = None


@dataclass
class Decision:
    links: list[DecidedLink] = field(default_factory=list)
    page_is_leaf: bool = False
    page_exclusion_reason: str | None = None
    raw_preview: str = ""
    parse_error: str | None = None


def _parse_decision(raw_content: str, candidates: list[LinkSignal]) -> Decision:
    raw = raw_content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Decision(raw_preview=raw[:500], parse_error=f"invalid_json: {exc}")
    allowed = {sig.url for sig in candidates}
    links: list[DecidedLink] = []
    for item in (data.get("links") or []):
        url = item.get("url")
        if url not in allowed:
            continue  # anti-hallucination: drop invented URLs
        label = item.get("label")
        if label not in _LABELS:
            label = "noise"
        raw_axis = item.get("facet_axis")
        facet_axis = raw_axis if (label == "reslice" and raw_axis in _RESLICE_AXES) else None
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        raw_reason = item.get("exclusion_reason")
        links.append(
            DecidedLink(
                url=url,
                label=label,
                confidence=conf,
                is_leaf=bool(item.get("is_leaf", False)),
                org_unit_name=item.get("org_unit_name"),
                exclusion_reason=raw_reason if is_valid_exclusion_reason(raw_reason) else None,
                facet_axis=facet_axis,
            )
        )
    page_reason = data.get("page_exclusion_reason")
    return Decision(
        links=links,
        page_is_leaf=bool(data.get("page_is_leaf", False)),
        page_exclusion_reason=page_reason if is_valid_exclusion_reason(page_reason) else None,
        raw_preview=raw[:500],
    )


async def decide_links(snapshot: PageSnapshot, candidates: list[LinkSignal],
                       node: DeciderNode, context: DeciderContext, *,
                       client: LLMClient) -> Decision:
    messages = build_decider_messages(
        snapshot, candidates, node, context, max_tokens=client.settings.llm_max_page_tokens
    )
    resp = await client.chat(messages, response_format={"type": "json_object"}, thinking=True)
    return _parse_decision(resp.content or "", candidates)
