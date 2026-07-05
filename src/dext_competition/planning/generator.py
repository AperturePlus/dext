"""C5 preparation-plan generator with grounded template fallback."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from dext_competition.contracts.plan import (
    PlanGenerationResult,
    PlanTask,
    PreparationPlanDraft,
)
from dext_competition.errors import CompetitionError, CompetitionErrorCode, ErrorSeverity
from dext_competition.planning.profile import (
    DEFAULT_PLAN_PROFILE_PATH,
    PlanGenerationProfile,
    load_plan_generation_profile,
)
from dext_competition.planning.scheduler import PlanSchedulingError, schedule_plan
from dext_competition.planning.schemas import PlanGenerationRequest
from dext_competition.planning.templates import build_phase_templates
from dext_competition.planning.validation import validate_personalization_output, validate_plan
from dext_competition.ports import CompetitionCatalogPort, ConstrainedGenerationPipeline
from dext_grounded import ContentClass, FactBundle, FactItem


@dataclass(frozen=True, slots=True)
class PlanGeneratorDeps:
    catalog_port: CompetitionCatalogPort
    generation_pipeline: ConstrainedGenerationPipeline | None = None
    generation_profile: PlanGenerationProfile | None = None
    generation_profile_path: str | Path = DEFAULT_PLAN_PROFILE_PATH


def _issue(code: CompetitionErrorCode, severity: ErrorSeverity, message: str) -> CompetitionError:
    return CompetitionError(code, severity, message)


def _plan_id(request: PlanGenerationRequest) -> str:
    identity = "|".join((
        request.competition_id,
        request.calendar_today.isoformat(),
        request.target_date.isoformat(),
        request.time_model,
        str(request.weekly_hours),
        request.experience_level,
    ))
    return "plan_" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex


def _validate_request(request: PlanGenerationRequest) -> str | None:
    if not request.competition_id:
        return "competition_id is required"
    if request.time_model not in {"submission_deadline", "competition_window"}:
        return "unsupported time_model"
    if request.experience_level not in {"beginner", "intermediate", "experienced"}:
        return "unsupported experience_level"
    if request.weekly_hours <= 0:
        return "weekly_hours must be positive"
    if request.target_date <= request.calendar_today:
        return "target_date must be after calendar_today"
    if request.time_model == "competition_window" and request.defense_date is not None:
        return "competition_window must not define defense_date"
    if request.time_model == "submission_deadline" and request.event_end_date is not None:
        return "submission_deadline must not define event_end_date"
    return None


def _fact_bundle(card, build_id: str) -> FactBundle:
    refs = tuple(card.internal_source_refs)
    facts = tuple(
        FactItem(field, value, ContentClass.FACT, refs)
        for field, value in (
            ("category", card.category),
            ("summary", card.summary),
            ("eligibility", card.eligibility),
            ("preparation_focus", "; ".join(card.preparation_focus)),
        )
        if value
    )
    return FactBundle(build_id, card.competition_id, facts, refs)


def _fallback_warning(message: str) -> CompetitionError:
    return _issue(CompetitionErrorCode.GENERATION_FALLBACK, ErrorSeverity.WARNING, message)


async def _personalize(
    request: PlanGenerationRequest,
    card,
    draft: PreparationPlanDraft,
    *,
    pipeline: ConstrainedGenerationPipeline | None,
    profile: PlanGenerationProfile | None,
    build_id: str,
) -> tuple[PreparationPlanDraft, tuple[CompetitionError, ...]]:
    if pipeline is None or profile is None:
        return draft, (_fallback_warning("generation unavailable; grounded template used"),)
    try:
        generated = await asyncio.wait_for(
            pipeline.generate(
                system_prompt_id=profile.system_prompt_id,
                user_inputs={
                    "competition_id": request.competition_id,
                    "time_model": request.time_model,
                    "experience_level": request.experience_level,
                    "weekly_hours": request.weekly_hours,
                    "phase_keys": [phase.key for phase in draft.phases],
                },
                fact_bundle=_fact_bundle(card, build_id),
                student_context=request.student_context,
                json_schema=dict(profile.json_schema),
                generation_profile_version=profile.version,
                safety_domain=profile.safety_domain,
                operation_id=profile.operation_id,
            ),
            timeout=profile.timeout_seconds,
        )
    except (TimeoutError, OSError, RuntimeError):
        return draft, (_fallback_warning("generation provider failed; grounded template used"),)
    if generated.warnings:
        return draft, (_fallback_warning("generated personalization was rejected; grounded template used"),)
    output = validate_personalization_output(
        generated.output,
        known_phase_keys={phase.key for phase in draft.phases},
        profile=profile,
    )
    if output is None:
        return draft, (_fallback_warning("generated personalization failed schema validation; grounded template used"),)

    phase_by_key = {phase.key: phase for phase in draft.phases}
    blocked = set(request.constraints.exam_dates) | set(request.constraints.unavailable_dates)
    additions: list[PlanTask] = []
    total_weeks = max(1.0, (request.target_date - request.calendar_today).days / 7)
    total_budget = request.weekly_hours * total_weeks
    used_hours = sum(task.estimated_hours or 0.0 for task in draft.optional_tasks)
    for phase_key, tasks in output.items():
        phase = phase_by_key[phase_key]
        due = phase.end_date
        while due >= phase.start_date and due in blocked:
            due -= timedelta(days=1)
        if due < phase.start_date:
            continue
        for index, task in enumerate(tasks):
            task_hours = float(task["estimated_hours"])
            if used_hours + task_hours > total_budget:
                return draft, (_fallback_warning(
                    "generated personalization exceeds the available weekly-hours budget; grounded template used"
                ),)
            additions.append(PlanTask(
                task_id=f"task:{phase_key}:ai:{index}",
                phase_id=phase.phase_id,
                label=str(task["title"]),
                kind="optional",
                estimated_hours=task_hours,
                start_date=phase.start_date,
                due_date=due,
            ))
            used_hours += task_hours
    additions_by_phase: dict[str, list[str]] = {}
    for task in additions:
        additions_by_phase.setdefault(task.phase_id, []).append(task.task_id)
    personalized_phases = tuple(
        replace(
            phase,
            task_ids=(*phase.task_ids, *additions_by_phase.get(phase.phase_id, ())),
        )
        for phase in draft.phases
    )
    personalized = PreparationPlanDraft(
        plan_id=draft.plan_id,
        competition_id=draft.competition_id,
        time_model=draft.time_model,
        target_date=draft.target_date,
        event_end_date=draft.event_end_date,
        defense_date=draft.defense_date,
        phases=personalized_phases,
        tasks=draft.tasks,
        optional_tasks=(*draft.optional_tasks, *additions),
        milestones=draft.milestones,
        calendar_items=draft.calendar_items,
        risk_register=draft.risk_register,
        internal_source_refs=draft.internal_source_refs,
        warnings=draft.warnings,
        revision=draft.revision,
    )
    return personalized, ()


class PreparationPlanGenerator:
    def __init__(self, deps: PlanGeneratorDeps) -> None:
        self._deps = deps

    async def generate(self, request: PlanGenerationRequest) -> PlanGenerationResult:
        invalid = _validate_request(request)
        if invalid:
            issue = _issue(CompetitionErrorCode.PLAN_INVALID, ErrorSeverity.ERROR, invalid)
            return PlanGenerationResult(None, (issue,))
        card = await self._deps.catalog_port.get(request.competition_id)
        if card is None:
            issue = _issue(CompetitionErrorCode.CATALOG_UNAVAILABLE, ErrorSeverity.ERROR, "competition not found")
            return PlanGenerationResult(None, (issue,))
        templates = build_phase_templates(
            card, time_model=request.time_model, experience_level=request.experience_level,
        )
        try:
            scheduled = schedule_plan(request, templates)
        except PlanSchedulingError as exc:
            issue = _issue(CompetitionErrorCode.PLAN_INVALID, ErrorSeverity.ERROR, str(exc))
            return PlanGenerationResult(None, (issue,))

        warnings: list[CompetitionError] = []
        if scheduled.dropped_optional_labels:
            warnings.append(_issue(
                CompetitionErrorCode.PLAN_INVALID,
                ErrorSeverity.WARNING,
                "optional tasks omitted because of budget or unavailable dates",
            ))
        if request.time_model == "competition_window" and request.event_end_date is None:
            warnings.append(_issue(
                CompetitionErrorCode.KNOWLEDGE_BASE_STALE,
                ErrorSeverity.WARNING,
                "event end date is uncertain; verify the current competition notice",
            ))
        draft = PreparationPlanDraft(
            plan_id=_plan_id(request),
            competition_id=request.competition_id,
            time_model=request.time_model,
            target_date=request.target_date,
            event_end_date=request.event_end_date,
            defense_date=(request.defense_date or request.target_date + timedelta(days=14))
            if request.time_model == "submission_deadline" else None,
            phases=scheduled.phases,
            tasks=scheduled.tasks,
            optional_tasks=scheduled.optional_tasks,
            milestones=tuple(phase.label for phase in scheduled.phases),
            calendar_items=tuple(
                f"{phase.key}:{phase.start_date.isoformat()}:{phase.end_date.isoformat()}"
                for phase in scheduled.phases
            ),
            risk_register=tuple(card.risk_flags),
            internal_source_refs=card.internal_source_refs,
            warnings=tuple(warnings),
            revision=0,
        )
        validation_issues = validate_plan(draft)
        if validation_issues:
            issue = _issue(
                CompetitionErrorCode.PLAN_INVALID,
                ErrorSeverity.ERROR,
                "; ".join(validation_issues),
            )
            return PlanGenerationResult(None, (issue,))

        try:
            profile = self._deps.generation_profile or load_plan_generation_profile(
                self._deps.generation_profile_path
            )
        except (OSError, KeyError, TypeError, ValueError):
            profile = None
        personalized, generation_issues = await _personalize(
            request,
            card,
            draft,
            pipeline=self._deps.generation_pipeline,
            profile=profile,
            build_id=self._deps.catalog_port.manifest().knowledge_base_version,
        )
        all_issues = (*warnings, *generation_issues)
        if generation_issues:
            personalized = PreparationPlanDraft(
                plan_id=personalized.plan_id,
                competition_id=personalized.competition_id,
                time_model=personalized.time_model,
                target_date=personalized.target_date,
                event_end_date=personalized.event_end_date,
                defense_date=personalized.defense_date,
                phases=personalized.phases,
                tasks=personalized.tasks,
                optional_tasks=personalized.optional_tasks,
                milestones=personalized.milestones,
                calendar_items=personalized.calendar_items,
                risk_register=personalized.risk_register,
                internal_source_refs=personalized.internal_source_refs,
                warnings=all_issues,
                revision=personalized.revision,
            )
        return PlanGenerationResult(
            personalized,
            all_issues,
            profile.version if profile else "competition.plan.v1",
        )


__all__ = ["PlanGeneratorDeps", "PreparationPlanGenerator"]
