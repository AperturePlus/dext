"""C6 preparation-plan assistant service."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from dext_competition.assistant.change_cards import (
    cards_from_generation_output,
    compact_plan_snapshot,
    reply_from_generation_output,
)
from dext_competition.assistant.profile import (
    DEFAULT_ASSISTANT_PROFILE_PATH,
    AssistantGenerationProfile,
    load_assistant_generation_profile,
)
from dext_competition.assistant.validation import validate_change_cards
from dext_competition.contracts.assistant import (
    AssistantHistoryTurn,
    PlanAssistantRequest,
    PlanAssistantResult,
    PlanAssistantServiceResult,
    PlanChangeCard,
    PlanChangeSet,
)
from dext_competition.contracts.knowledge import KnowledgeHit
from dext_competition.errors import CompetitionError, CompetitionErrorCode, ErrorSeverity
from dext_competition.ports import ConstrainedGenerationPipeline, KnowledgeIndexPort
from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlanAssistantDeps:
    generation_pipeline: ConstrainedGenerationPipeline | None
    knowledge_index: KnowledgeIndexPort | None = None
    generation_profile: AssistantGenerationProfile | None = None
    generation_profile_path: str | Path = DEFAULT_ASSISTANT_PROFILE_PATH


def _issue(code: CompetitionErrorCode, severity: ErrorSeverity, message: str) -> CompetitionError:
    return CompetitionError(code, severity, message)


def _history_payload(history: tuple[AssistantHistoryTurn, ...]) -> list[dict[str, object]]:
    return [
        {
            "role": turn.role,
            "content": turn.content,
            "card_results": [
                {"card_id": item.card_id, "status": item.status}
                for item in turn.card_results
            ],
        }
        for turn in history
    ]


def _dedupe_refs(refs: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    out: list[SourceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = (ref.doc_path, ref.heading_path, ref.chunk_hash)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return tuple(out)


def _facts_from_hits(hits: tuple[KnowledgeHit, ...]) -> tuple[FactItem, ...]:
    return tuple(
        FactItem(
            field="knowledge_chunk",
            value=hit.chunk.text,
            content_class=ContentClass.FACT,
            source_refs=(hit.source_ref,),
        )
        for hit in hits
    )


async def _fact_bundle(request: PlanAssistantRequest, deps: PlanAssistantDeps) -> FactBundle:
    plan = request.plan_snapshot
    build_id = "competition.assistant.local"
    facts: list[FactItem] = []
    refs: list[SourceRef] = list(plan.internal_source_refs)
    if plan.internal_source_refs:
        facts.extend([
            FactItem("plan.time_model", plan.time_model, ContentClass.FACT, plan.internal_source_refs),
            FactItem("plan.risk_register", "；".join(plan.risk_register), ContentClass.FACT, plan.internal_source_refs),
            FactItem("plan.milestones", "；".join(plan.milestones), ContentClass.FACT, plan.internal_source_refs),
        ])
    if deps.knowledge_index is not None:
        manifest = deps.knowledge_index.manifest()
        build_id = manifest.version_id
        hits = await deps.knowledge_index.query(request.user_message, limit=6)
        facts.extend(_facts_from_hits(tuple(hits)))
        refs.extend(hit.source_ref for hit in hits)
    return FactBundle(
        build_id=build_id,
        subject_id=plan.competition_id,
        facts=tuple(facts),
        source_refs=_dedupe_refs(tuple(refs)),
    )


def _reply(output: dict | str, cards_count: int) -> str:
    reply = reply_from_generation_output(output).strip()
    if reply:
        return reply
    if cards_count:
        return "已生成可供审批的备赛计划改动建议。"
    return "没有生成可用的备赛计划改动建议。"


def _warning_issues(warnings) -> tuple[CompetitionError, ...]:
    issues: list[CompetitionError] = []
    for warning in warnings:
        code = getattr(warning, "code", "")
        if code in {"unsafe_advice", "content_policy_refusal"}:
            issues.append(_issue(
                CompetitionErrorCode.UNSAFE_ADVICE,
                ErrorSeverity.WARNING,
                getattr(warning, "message", "unsafe assistant advice was rejected"),
            ))
        elif code in {"no_grounded_output", "insufficient_facts", "fact_ref_missing"}:
            issues.append(_issue(
                CompetitionErrorCode.CHANGE_CARD_REJECTED,
                ErrorSeverity.WARNING,
                getattr(warning, "message", "assistant output lacked grounded support"),
            ))
    return tuple(issues)


def _local_fallback_result(
    request: PlanAssistantRequest,
    *,
    generation_profile_version: str,
) -> PlanAssistantServiceResult:
    plan = request.plan_snapshot
    advice = (
        "我先按当前计划给出一条稳妥建议：保留现有阶段安排，"
        "本周优先完成最早截止的未完成任务，并在下一次训练后复盘进度。"
    )
    cards: tuple[PlanChangeCard, ...] = ()
    if plan.phases:
        cards = (
            PlanChangeCard(
                id=f"card:{request.request_id}:advice",
                type="append_advice",
                target_phase_key=plan.phases[0].key,
                advice_text=advice,
                summary="补充本周备赛建议",
                rationale="本地联调未配置助手 LLM 时，使用当前计划快照生成保守建议。",
                validation_status="passed",
            ),
        )
    result = PlanAssistantResult(
        reply="已根据当前备赛计划生成一条本地建议，可先作为调整参考。",
        change_set=PlanChangeSet(
            id=f"changes:{request.request_id}",
            base_plan_revision=request.base_plan_revision,
            cards=cards,
        ),
        request_id=request.request_id,
    )
    return PlanAssistantServiceResult(
        result,
        (),
        generation_profile_version=generation_profile_version,
        diagnostics={"fallback": "local"},
    )


async def suggest_plan_changes(
    request: PlanAssistantRequest,
    deps: PlanAssistantDeps,
) -> PlanAssistantServiceResult:
    profile = deps.generation_profile or load_assistant_generation_profile(
        deps.generation_profile_path
    )
    if request.base_plan_revision != request.plan_snapshot.revision:
        return PlanAssistantServiceResult(
            None,
            (_issue(
                CompetitionErrorCode.PLAN_REVISION_STALE,
                ErrorSeverity.ERROR,
                "base_plan_revision does not match the plan snapshot revision",
            ),),
            generation_profile_version=profile.version,
        )
    if deps.generation_pipeline is None:
        return _local_fallback_result(
            request,
            generation_profile_version="competition.assistant.local-fallback-v1",
        )
    try:
        bundle = await _fact_bundle(request, deps)
        generated = await asyncio.wait_for(
            deps.generation_pipeline.generate(
                system_prompt_id=profile.system_prompt_id,
                user_inputs={
                    "calendar_today": request.calendar_today.isoformat(),
                    "base_plan_revision": request.base_plan_revision,
                    "plan_snapshot": compact_plan_snapshot(request.plan_snapshot),
                    "user_message": request.user_message,
                    "request_id": request.request_id,
                    "history": _history_payload(request.history),
                },
                fact_bundle=bundle,
                student_context=request.student_context,
                json_schema=dict(profile.json_schema),
                generation_profile_version=profile.version,
                safety_domain=profile.safety_domain,
                include_contacts=False,
                operation_id=profile.operation_id,
            ),
            timeout=profile.timeout_seconds,
        )
    except Exception as exc:
        logger.warning(
            "competition assistant generation failed; using local fallback error_type=%s",
            type(exc).__name__,
        )
        fallback = _local_fallback_result(
            request,
            generation_profile_version="competition.assistant.local-fallback-v1",
        )
        return PlanAssistantServiceResult(
            fallback.result,
            (_issue(
                CompetitionErrorCode.GENERATION_FALLBACK,
                ErrorSeverity.WARNING,
                "assistant generation failed; local fallback was used",
            ),),
            generation_profile_version=fallback.generation_profile_version,
            diagnostics=fallback.diagnostics,
        )

    warning_issues = _warning_issues(generated.warnings)
    blocking_warning = next(
        (
            warning for warning in generated.warnings
            if getattr(warning, "code", "") in {
                "llm_unavailable",
                "generation_parse_error",
                "schema_validation_failed",
                "json_parse_failed",
            }
        ),
        None,
    )
    if blocking_warning is not None:
        logger.warning(
            "competition assistant generation warning; using local fallback code=%s",
            getattr(blocking_warning, "code", ""),
        )
        fallback = _local_fallback_result(
            request,
            generation_profile_version="competition.assistant.local-fallback-v1",
        )
        return PlanAssistantServiceResult(
            fallback.result,
            (_issue(
                CompetitionErrorCode.GENERATION_FALLBACK,
                ErrorSeverity.WARNING,
                getattr(blocking_warning, "message", "assistant generation fallback was used"),
            ),),
            generation_profile_version=fallback.generation_profile_version,
            diagnostics=fallback.diagnostics,
        )
    if any(issue.code == CompetitionErrorCode.UNSAFE_ADVICE for issue in warning_issues):
        raw_cards = ()
    else:
        raw_cards = cards_from_generation_output(
            generated.output,
            source_refs=tuple(generated.cited_refs),
            max_cards=profile.max_cards,
        )
    cards = validate_change_cards(
        request.plan_snapshot,
        raw_cards,
        calendar_today=request.calendar_today,
    )
    issues = list(warning_issues)
    if raw_cards and not any(card.status == "pending" for card in cards):
        issues.append(_issue(
            CompetitionErrorCode.CHANGE_CARD_REJECTED,
            ErrorSeverity.WARNING,
            "all assistant change cards were rejected by validation",
        ))
    change_set = PlanChangeSet(
        id=f"changes:{request.request_id}",
        base_plan_revision=request.base_plan_revision,
        cards=cards,
    )
    result = PlanAssistantResult(
        reply=_reply(generated.output, len(cards)),
        change_set=change_set,
        request_id=request.request_id,
    )
    return PlanAssistantServiceResult(
        result,
        tuple(issues),
        generation_profile_version=profile.version,
        diagnostics={
            "fact_count": len(bundle.facts),
            "source_ref_count": len(bundle.source_refs),
        },
    )


__all__ = ["PlanAssistantDeps", "suggest_plan_changes"]
