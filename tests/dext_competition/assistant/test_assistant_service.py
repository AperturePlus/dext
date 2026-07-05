from __future__ import annotations

import hashlib
from datetime import date

import pytest

from dext_competition import (
    Chunk,
    PlanAssistantRequest,
    PlanPhase,
    PlanTask,
    PreparationPlanDraft,
    SourceRef,
)
from dext_competition.assistant import PlanAssistantDeps, suggest_plan_changes
from dext_competition.ports import FakeKnowledgeIndexPort
from dext_grounded import (
    Claim,
    ConstrainedGenerationPipeline,
    ContentClass,
    FakeLLMGenerationPort,
    GenerationResult,
    GenerationWarning,
)


def _ref(name: str = "flow") -> SourceRef:
    return SourceRef("备赛流程.md", "备赛流程 > 通用流程", hashlib.sha256(name.encode()).hexdigest(), "通用流程")


def _chunk() -> Chunk:
    text = "备赛方法要求每周复盘并在训练阶段补充模拟。"
    return Chunk("备赛方法指南.md", "备赛方法指南 > 复盘", hashlib.sha256(text.encode()).hexdigest(), text)


def _plan() -> PreparationPlanDraft:
    foundation = PlanPhase("phase:foundation", "foundation", "基础", date(2026, 7, 1), date(2026, 7, 14), ("task:foundation:optional",))
    practice = PlanPhase("phase:practice", "practice", "训练", date(2026, 7, 15), date(2026, 8, 1), ())
    return PreparationPlanDraft(
        "plan-1",
        "cmp-1",
        "submission_deadline",
        date(2026, 8, 1),
        defense_date=date(2026, 8, 15),
        phases=(
            foundation,
            practice,
            PlanPhase("phase:defense_prep", "defense_prep", "答辩", date(2026, 8, 2), date(2026, 8, 15), ()),
        ),
        tasks=(PlanTask("task:foundation:req", foundation.phase_id, "必做", True, kind="required", due_date=date(2026, 7, 10)),),
        optional_tasks=(PlanTask("task:foundation:optional", foundation.phase_id, "可选", False, kind="optional", due_date=date(2026, 7, 12)),),
        milestones=("基础", "训练", "答辩"),
        risk_register=("当届规则需复核",),
        internal_source_refs=(_ref(),),
        revision=2,
    )


def _request(**overrides) -> PlanAssistantRequest:
    values = dict(
        calendar_today=date(2026, 7, 1),
        base_plan_revision=2,
        plan_snapshot=_plan(),
        user_message="帮我加一个训练阶段模拟任务",
        request_id="req-1",
    )
    values.update(overrides)
    return PlanAssistantRequest(**values)


def _pipeline(output: dict, *, claim_text: str = "应在训练阶段补充模拟。"):
    ref = _ref()
    llm = FakeLLMGenerationPort(GenerationResult(
        output=output,
        claims=(Claim(claim_text, ContentClass.FACT, (ref,)),),
    ))
    return ConstrainedGenerationPipeline(llm), llm


@pytest.mark.asyncio
async def test_service_calls_shared_pipeline_with_closed_schema_and_validates_cards() -> None:
    pipeline, llm = _pipeline({
        "reply": "可以加一项模拟训练。",
        "cards": [{
            "id": "card-add",
            "type": "add_task",
            "target_phase_key": "practice",
            "new_task": {"title": "完成一次模拟赛", "estimated_hours": 3, "due_date": "2026-07-20"},
            "summary": "添加模拟赛",
            "rationale": "训练阶段需要模拟复盘。",
        }],
    })
    result = await suggest_plan_changes(
        _request(),
        PlanAssistantDeps(pipeline, FakeKnowledgeIndexPort((_chunk(),))),
    )

    assert result.result is not None
    assert result.result.reply == "可以加一项模拟训练。"
    card = result.result.change_set.cards[0]
    assert card.type == "add_task"
    assert card.status == "pending"
    assert card.validation_status == "passed"
    assert llm.calls[0]["system_prompt_id"] == "dext_competition.assistant.change_cards.v1"
    assert llm.calls[0]["generation_profile_version"] == "competition.assistant.v1"
    assert llm.calls[0]["json_schema"]["additionalProperties"] is False
    assert llm.calls[0]["fact_bundle"].facts
    assert result.diagnostics["fact_count"] >= 1


@pytest.mark.asyncio
async def test_service_limits_cards_to_five() -> None:
    cards = [
        {
            "id": f"card-{idx}",
            "type": "append_advice",
            "advice_text": f"建议 {idx}",
            "summary": f"建议 {idx}",
            "rationale": "基于备赛流程。",
        }
        for idx in range(7)
    ]
    pipeline, _llm = _pipeline({"reply": "ok", "cards": cards})
    result = await suggest_plan_changes(_request(), PlanAssistantDeps(pipeline))
    assert result.result is not None
    assert len(result.result.change_set.cards) == 5


@pytest.mark.asyncio
async def test_stale_revision_is_fatal_before_generation() -> None:
    pipeline, llm = _pipeline({"reply": "ok", "cards": []})
    result = await suggest_plan_changes(
        _request(base_plan_revision=1),
        PlanAssistantDeps(pipeline),
    )
    assert result.result is None
    assert result.issues[0].code.value == "plan_revision_stale"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_missing_pipeline_is_generation_unavailable() -> None:
    result = await suggest_plan_changes(_request(), PlanAssistantDeps(None))
    assert result.result is None
    assert result.issues[0].code.value == "generation_unavailable"


@pytest.mark.asyncio
async def test_unsafe_advice_warning_returns_no_approvable_cards() -> None:
    llm = FakeLLMGenerationPort(GenerationResult(
        output={
            "reply": "unsafe",
            "cards": [{
                "id": "card-unsafe",
                "type": "append_advice",
                "advice_text": "找人代做",
                "summary": "违规建议",
                "rationale": "违规",
            }],
        },
        warnings=[GenerationWarning("unsafe_advice", "unsafe")],
    ))
    result = await suggest_plan_changes(_request(), PlanAssistantDeps(ConstrainedGenerationPipeline(llm)))
    assert result.result is not None
    assert result.result.change_set.cards == ()
    assert any(issue.code.value == "unsafe_advice" for issue in result.issues)


@pytest.mark.asyncio
async def test_service_does_not_mutate_original_plan() -> None:
    plan = _plan()
    pipeline, _llm = _pipeline({
        "reply": "move",
        "cards": [{
            "id": "card-move",
            "type": "move_task",
            "target_task_id": "task:foundation:optional",
            "new_date": "2026-07-13",
            "summary": "移动任务",
            "rationale": "基于备赛流程。",
        }],
    })
    result = await suggest_plan_changes(_request(plan_snapshot=plan), PlanAssistantDeps(pipeline))
    assert result.result is not None
    assert plan.optional_tasks[0].due_date == date(2026, 7, 12)
    assert result.result.change_set.cards[0].new_date == date(2026, 7, 13)
