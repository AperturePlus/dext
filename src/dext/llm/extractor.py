"""Leaf extractor (spec §4, source doc §7.2).

Asks the model to call the save_professors tool, sanitizes records, and classifies
the two recoverable failure modes. Payloads come out already sanitized (the SP2
contract). SP5 classifies only — SP6 owns retries, state transitions, and logging.

DeepSeek V4 note: thinking mode rejects a FORCED tool_choice, so we pass
tool_choice="auto" and rely on the prompt to drive the call; a no-call/empty
result flows into the no_structured_data assessment below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dext.exclusions import is_valid_exclusion_reason
from dext.llm.client import LLMClient, LLMResponse
from dext.llm.prompts import SAVE_PROFESSORS_TOOL, build_extractor_messages
from dext.llm.retry import assess_no_data
from dext.llm.sanitizer import sanitize
from dext.page.links import PageSnapshot
from dext.types import ProfessorPayload


@dataclass
class OrgUnitContext:
    org_unit_id: int | None
    org_unit_name: str
    faculty_list_url: str = ""


@dataclass
class ExtractionResult:
    payloads: list[ProfessorPayload] = field(default_factory=list)
    failure_type: str | None = None  # invalid_json | no_structured_data | excluded | None
    raw_preview: str = ""
    recoverable: bool | None = None  # meaningful only for no_structured_data
    exclusion_reason: str | None = None  # set when failure_type == "excluded"


def _result_from_response(resp: LLMResponse, snapshot: PageSnapshot) -> ExtractionResult:
    if resp.invalid_tool_calls:
        raw = resp.invalid_tool_calls[0].get("arguments_raw") or ""
        return ExtractionResult(failure_type="invalid_json", raw_preview=raw[:500])

    records: list[dict] = []
    exclusion_reason: str | None = None
    for tc in resp.tool_calls:
        if tc.get("name") == "save_professors":
            args = tc["arguments"]
            records.extend(args.get("professors") or [])
            er = args.get("exclusion_reason")
            if exclusion_reason is None and is_valid_exclusion_reason(er):
                exclusion_reason = er

    payloads = [p for p in (sanitize(r) for r in records) if p is not None]
    if payloads:
        return ExtractionResult(payloads=payloads, raw_preview=(resp.content or "")[:500])

    if exclusion_reason:
        return ExtractionResult(
            failure_type="excluded",
            exclusion_reason=exclusion_reason,
            raw_preview=(resp.content or "")[:500],
        )

    verdict = assess_no_data(snapshot)
    return ExtractionResult(
        failure_type="no_structured_data",
        raw_preview=(resp.content or "")[:500],
        recoverable=verdict.recoverable,
    )


async def extract_professors(snapshot: PageSnapshot, org_unit_ctx: OrgUnitContext, *,
                             client: LLMClient, attempt: int = 0) -> ExtractionResult:
    strict = attempt > 0
    messages = build_extractor_messages(
        snapshot, org_unit_ctx, max_tokens=client.settings.llm_max_page_tokens, strict=strict
    )
    resp = await client.chat(
        messages, tools=[SAVE_PROFESSORS_TOOL], tool_choice="auto",
        thinking=True, retry_mode=strict,
    )
    return _result_from_response(resp, snapshot)
