from __future__ import annotations

import hashlib
from datetime import date, timedelta

from dext_competition import CompetitionCard, SourceRef
from dext_competition.planning import (
    PlanConstraints,
    PlanGenerationRequest,
    PlanGeneratorDeps,
    PreparationPlanGenerator,
)
from dext_competition.ports import FakeCompetitionCatalogPort
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult


def _ref() -> SourceRef:
    return SourceRef("备赛流程.md", "备赛流程 > 通用流程", hashlib.sha256(b"flow").hexdigest(), "通用备赛流程")


def _card() -> CompetitionCard:
    return CompetitionCard(
        "cmp-1", "测试竞赛", "计算机", summary="算法与工程实践",
        preparation_focus=("算法训练",), risk_flags=("当届规则需复核",),
        internal_source_refs=(_ref(),),
    )


def _request(**overrides) -> PlanGenerationRequest:
    values = dict(
        competition_id="cmp-1",
        calendar_today=date(2026, 7, 1),
        target_date=date(2026, 8, 20),
        weekly_hours=8,
        experience_level="beginner",
        time_model="submission_deadline",
    )
    values.update(overrides)
    return PlanGenerationRequest(**values)


async def test_provider_absent_returns_grounded_template_with_warning() -> None:
    generator = PreparationPlanGenerator(PlanGeneratorDeps(FakeCompetitionCatalogPort((_card(),))))
    result = await generator.generate(_request())
    assert result.draft is not None
    assert any(issue.code.value == "generation_fallback" for issue in result.issues)
    assert all(task.is_mandatory for task in result.draft.tasks)
    assert any(phase.key == "defense_prep" for phase in result.draft.phases)
    assert result.draft.internal_source_refs == (_ref(),)


async def test_valid_personalization_only_adds_optional_known_phase_tasks() -> None:
    pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "phases": [{
            "key": "practice",
            "optional_tasks": [{"title": "完成额外代码复现", "estimated_hours": 5}],
        }]
    })))
    generator = PreparationPlanGenerator(PlanGeneratorDeps(
        FakeCompetitionCatalogPort((_card(),)), generation_pipeline=pipeline,
    ))
    result = await generator.generate(_request(experience_level="experienced"))
    assert result.draft is not None
    added = next(task for task in result.draft.optional_tasks if task.label == "完成额外代码复现")
    practice = next(phase for phase in result.draft.phases if phase.key == "practice")
    assert added.task_id in practice.task_ids
    assert not any(issue.code.value == "generation_fallback" for issue in result.issues)


async def test_personalization_over_budget_falls_back_to_template() -> None:
    pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "phases": [{
            "key": "practice",
            "optional_tasks": [{"title": "超预算任务", "estimated_hours": 999}],
        }]
    })))
    generator = PreparationPlanGenerator(PlanGeneratorDeps(
        FakeCompetitionCatalogPort((_card(),)), generation_pipeline=pipeline,
    ))
    result = await generator.generate(_request(weekly_hours=1))
    assert result.draft is not None
    assert not any(task.label == "超预算任务" for task in result.draft.optional_tasks)
    assert any(issue.code.value == "generation_fallback" for issue in result.issues)


async def test_unknown_llm_phase_falls_back_without_mutating_required_tasks() -> None:
    pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "phases": [{"key": "unknown", "optional_tasks": []}]
    })))
    generator = PreparationPlanGenerator(PlanGeneratorDeps(
        FakeCompetitionCatalogPort((_card(),)), generation_pipeline=pipeline,
    ))
    result = await generator.generate(_request())
    assert result.draft is not None
    assert any(issue.code.value == "generation_fallback" for issue in result.issues)
    assert all(task.is_mandatory for task in result.draft.tasks)


async def test_impossible_required_schedule_returns_plan_invalid_without_draft() -> None:
    blocked = tuple(date(2026, 7, 1) + timedelta(days=offset) for offset in range(80))
    generator = PreparationPlanGenerator(PlanGeneratorDeps(FakeCompetitionCatalogPort((_card(),))))
    result = await generator.generate(_request(constraints=PlanConstraints(exam_dates=blocked)))
    assert result.draft is None
    assert result.issues[0].code.value == "plan_invalid"
