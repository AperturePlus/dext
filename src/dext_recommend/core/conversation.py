"""Conversation adapter — context validation, implicit classification, dispatch,
and detail_followup grounded generation (R5 spec).

Pure functions stay synchronous; LLM/store I/O is async. The dispatcher is the
sole public conversation entry point returning ConversationDispatchResult.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

from dext_grounded import (
    ConstrainedGenerationPipeline, ContentClass, FactBundle,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.core.generation_support import (
    map_generation_warnings as _map_generation_warnings,
    validate_fact_index_support as _detail_support_validator,
)
from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import (
    ConversationContext, ConversationDispatchResult, DetailFollowupResponse,
    RecommendRequest, RecommendationWarning,
)
from dext_recommend.core.intent import resolve_recommend_route

_VALID_INTENTS = {
    "new_search", "more_mentors", "same_field", "refine_direction", "detail_followup",
}
_VALID_SOURCES = {"explicit", "implicit", None}


class ConversationValidationError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(f"{code}: {safe_message}")


def _dedup_preserve_order(items) -> tuple:
    seen: set = set()
    out: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def _validate_context(
    context: ConversationContext,
    *,
    phase: Literal["input", "resolved"],
) -> ConversationContext:
    """Return a normalized copy; raise ConversationValidationError on failure."""
    src = context.intent_source
    intent = context.intent
    conf = context.intent_confidence

    if src not in _VALID_SOURCES:
        raise ConversationValidationError("invalid_intent", f"bad intent_source: {src!r}")
    if intent is not None and intent not in _VALID_INTENTS:
        raise ConversationValidationError("invalid_intent", f"bad intent: {intent!r}")

    if src == "explicit":
        if intent is None:
            raise ConversationValidationError("invalid_intent", "explicit requires intent")
        if conf is not None:
            raise ConversationValidationError("invalid_intent",
                                              "explicit must not carry confidence")
    elif src == "implicit":
        if phase == "input":
            if intent is not None or conf is not None:
                raise ConversationValidationError("invalid_intent",
                                                  "input implicit must be unclassified")
        else:  # resolved
            if intent not in _VALID_INTENTS:
                raise ConversationValidationError("invalid_intent",
                                                  "resolved implicit needs whitelist intent")
            if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= conf <= 1.0):
                raise ConversationValidationError("invalid_intent",
                                                  "resolved implicit confidence out of [0,1]")
    else:  # src is None
        if intent is not None or conf is not None:
            raise ConversationValidationError("invalid_intent",
                                              "None source must not carry intent/confidence")
        if phase == "input":
            context = replace(context, intent_source="implicit")

    # session/turn co-occurrence
    has_s = context.session_id is not None
    has_t = context.turn_id is not None
    if has_s != has_t:
        raise ConversationValidationError("invalid_conversation_state",
                                          "session_id and turn_id must co-occur")
    # fork pair co-occurrence
    has_m = context.main_session_id is not None
    has_st = context.source_turn_id is not None
    if has_m != has_st:
        raise ConversationValidationError("invalid_conversation_state",
                                          "main_session_id and source_turn_id must co-occur")

    deduped = _dedup_preserve_order(context.prior_result_entity_ids)
    if deduped != tuple(context.prior_result_entity_ids):
        context = replace(context, prior_result_entity_ids=deduped)
    return context


def _assemble_context(
    *, session_id, turn_id, main_session_id, source_turn_id,
    anchor_entity_id, intent, intent_source, intent_confidence,
    prior_result_entity_ids,
) -> ConversationContext:
    ctx = ConversationContext(
        session_id=session_id, turn_id=turn_id,
        main_session_id=main_session_id, source_turn_id=source_turn_id,
        anchor_entity_id=anchor_entity_id, intent=intent,
        intent_source=intent_source, intent_confidence=intent_confidence,
        prior_result_entity_ids=tuple(prior_result_entity_ids or ()),
    )
    return _validate_context(ctx, phase="input")


def _merge_stored_context(current: ConversationContext,
                          stored: ConversationContext | None) -> ConversationContext:
    if stored is None:
        return current
    return replace(
        current,
        anchor_entity_id=current.anchor_entity_id or stored.anchor_entity_id,
        main_session_id=current.main_session_id or stored.main_session_id,
        source_turn_id=current.source_turn_id or stored.source_turn_id,
        prior_result_entity_ids=_dedup_preserve_order(
            tuple(current.prior_result_entity_ids) + tuple(stored.prior_result_entity_ids)
        ),
    )


def _warn(code: RecommendationErrorCode, message: str, *, severity: str = "warning") -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity=severity)


class ConversationDispatcher:
    """Single public conversation entry point (R5 spec §5).

    Orchestrates: input validation -> snapshot/profile pin -> implicit classify
    (if needed, via ConstrainedGenerationPipeline) -> resolved validation ->
    route -> recommend/detail branch.  Returns ConversationDispatchResult.

    The instance holds only immutable deps/settings (deep immutability): the
    ``core``, ``pipeline`` and ``settings`` are set once at construction and never
    mutated.  Per-request mutable state (snapshot, exec_ctx) is kept local to
    each ``dispatch`` call.
    """

    def __init__(
        self,
        core,
        pipeline: ConstrainedGenerationPipeline,
        settings: RecommendSettings,
    ) -> None:
        self._core = core
        self._pipeline = pipeline
        self._settings = settings

    @property
    def core(self):
        return self._core

    async def dispatch(
        self,
        request: RecommendRequest,
        *,
        viewer_permissions=None,
        conversation_summary=None,
    ) -> ConversationDispatchResult:
        from dext_recommend.ports import ViewerPermissions
        vp = viewer_permissions or ViewerPermissions()
        ctx = request.conversation_context or ConversationContext()
        try:
            ctx = _validate_context(ctx, phase="input")
        except ConversationValidationError as e:
            code = RecommendationErrorCode(e.code)
            return ConversationDispatchResult(
                kind="error", context=None, recommendation=None, detail_followup=None,
                issues=(_warn(code, e.safe_message, severity="error"),),
            )

        store = self._core.deps.conversation_store
        if store is not None:
            stored = None
            if ctx.main_session_id and ctx.source_turn_id:
                stored = await store.resolve_fork(ctx.main_session_id, ctx.source_turn_id)
            elif ctx.session_id:
                stored = await store.load_context(ctx.session_id, ctx.turn_id)
            ctx = _merge_stored_context(ctx, stored)
            if conversation_summary is None and ctx.session_id:
                conversation_summary = await store.load_summary(
                    ctx.session_id, ctx.source_turn_id or ctx.turn_id,
                )

        # pin snapshot + generation profile once
        snapshot = self._core.deps.snapshot_port.get_snapshot()
        if snapshot is None:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                              "no ACTIVE build", severity="error"),),
            )
        gp_port = self._core.deps.generation_profile_port
        try:
            gen_profile = await gp_port.read_profile(self._settings.generation_profile_path)
        except Exception:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.GENERATION_UNAVAILABLE,
                              "generation profile unavailable", severity="error"),),
            )

        needs_classify = ctx is not None and ctx.intent_source == "implicit" and ctx.intent is None
        if needs_classify:
            ctx, classify_issue = await self._classify_implicit(
                ctx, snapshot, gen_profile, request, conversation_summary)
            if classify_issue is not None:
                if classify_issue.code == "needs_clarification":
                    return ConversationDispatchResult(
                        kind="clarification", context=ctx, recommendation=None,
                        detail_followup=None, issues=(classify_issue,),
                        generation_profile_version=gen_profile.version,
                    )
                return ConversationDispatchResult(
                    kind="error", context=ctx, recommendation=None, detail_followup=None,
                    issues=(classify_issue,), generation_profile_version=gen_profile.version,
                )
            try:
                ctx = _validate_context(ctx, phase="resolved")
            except ConversationValidationError as e:
                return ConversationDispatchResult(
                    kind="error", context=ctx, recommendation=None,
                    detail_followup=None,
                    issues=(_warn(RecommendationErrorCode(e.code), e.safe_message,
                                  severity="error"),),
                    generation_profile_version=gen_profile.version,
                )

        # route
        routed_req = replace(request, conversation_context=ctx)
        route = resolve_recommend_route(routed_req)
        if route.terminal_issues:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=tuple(route.terminal_issues),
                generation_profile_version=gen_profile.version,
            )

        if route.detail_followup:
            outcome = await self._resolve_detail_followup_pinned(
                ctx, snapshot, gen_profile, request, vp)
            if isinstance(outcome, ConversationDispatchResult):
                return outcome
            return ConversationDispatchResult(
                kind="detail_followup", context=ctx, recommendation=None,
                detail_followup=outcome, issues=(),
                generation_profile_version=gen_profile.version,
            )

        # recommend path
        from dext_recommend.core._resilience import RecommendExecutionContext
        exec_ctx = RecommendExecutionContext()
        exec_ctx.snapshot = snapshot
        exec_ctx.generation_profile_version = gen_profile.version
        try:
            resp = await asyncio.wait_for(
                self._core._recommend_pinned(
                    routed_req, vp, exec_ctx, snapshot=snapshot,
                    generation_profile=gen_profile,
                ),
                timeout=self._settings.total_timeout,
            )
        except asyncio.TimeoutError:
            resp = self._core._error_response_public(
                snapshot=snapshot, exec_ctx=exec_ctx,
                code=RecommendationErrorCode.REQUEST_TIMEOUT,
                message=f"recommend exceeded {self._settings.total_timeout}s",
                generation_profile_version=gen_profile.version,
            )
        resp = replace(resp, generation_profile_version=gen_profile.version)
        return ConversationDispatchResult(
            kind="recommendation", context=ctx, recommendation=resp,
            detail_followup=None, issues=tuple(route.warnings),
            generation_profile_version=gen_profile.version,
        )

    async def _classify_implicit(self, ctx, snapshot, gen_profile, request, conversation_summary):
        op = gen_profile.operations["implicit_intent"]
        empty_bundle = FactBundle(
            build_id=snapshot.build_id, subject_id="implicit-intent",
            facts=(), source_refs=(),
        )
        summary_text = conversation_summary.text if conversation_summary is not None else ""
        try:
            result = await asyncio.wait_for(self._pipeline.generate(
                system_prompt_id=op.system_prompt_id,
                user_inputs={
                    "query_text": request.query_text[: op.query_max_chars],
                    "conversation_summary": summary_text[: op.summary_max_chars],
                    "has_anchor": ctx.anchor_entity_id is not None,
                    "has_prior_results": bool(ctx.prior_result_entity_ids),
                },
                fact_bundle=empty_bundle, student_context=None,
                json_schema=op.json_schema,
                generation_profile_version=gen_profile.version,
                safety_domain="recommend", include_contacts=False,
                operation_id="implicit_intent",
            ), timeout=op.timeout)
        except Exception:
            return ctx, _warn(RecommendationErrorCode.INTENT_CLASSIFICATION_UNAVAILABLE,
                              "implicit intent classification failed", severity="error")
        output = result.output
        if not isinstance(output, dict):
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit output not a dict")
        intent = output.get("intent")
        confidence = output.get("confidence")
        if any(w.code == "content_policy_refusal" for w in result.warnings):
            return ctx, _warn(
                RecommendationErrorCode.CONTENT_POLICY_REFUSAL,
                "request refused by content policy",
                severity="error",
            )
        blocking = {"generation_unavailable", "generation_parse_error",
                    "json_parse_failed", "schema_validation_failed", "unsafe_advice"}
        if any(w.code in blocking for w in result.warnings):
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit intent output was not usable")
        if intent not in _VALID_INTENTS or not isinstance(confidence, (int, float)) \
                or isinstance(confidence, bool) or not (0.0 <= confidence <= 1.0):
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit intent illegal/missing")
        if confidence < op.confidence_threshold:
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit intent below confidence threshold")
        ctx = replace(ctx, intent_source="implicit", intent=intent, intent_confidence=float(confidence))
        return ctx, None

    async def _resolve_detail_followup_pinned(self, ctx, snapshot, gen_profile, request, vp):
        try:
            detail = await self._core.deps.facts_port.get_detail(
                snapshot, ctx.anchor_entity_id, include_contacts=False,
                viewer_permissions=vp,
            )
        except (KeyError, LookupError):
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                              "anchor is not available in the ACTIVE build", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        if detail.role_status in {"excluded", "review"} and not (
            detail.role_status == "review" and vp.can_view_review
        ):
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                              "anchor is not available in the ACTIVE build", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        op = gen_profile.operations["detail_followup"]
        try:
            result = await asyncio.wait_for(self._pipeline.generate(
                system_prompt_id=op.system_prompt_id,
                user_inputs={"question": request.query_text,
                             "display_name": detail.display_name},
                fact_bundle=detail.fact_bundle,
                student_context=request.student_context,
                json_schema=dict(op.json_schema),
                generation_profile_version=gen_profile.version,
                safety_domain="recommend", include_contacts=False,
                operation_id="detail_followup",
                subject_kind="mentor",
                support_validator=_detail_support_validator,
            ), timeout=op.timeout)
        except Exception:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.FOLLOWUP_GENERATION_UNAVAILABLE,
                              "detail follow-up generation failed", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        mapped = _map_generation_warnings(result.warnings)
        if any(w.severity == "error" for w in mapped):
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=mapped, generation_profile_version=gen_profile.version,
            )
        output = result.output
        answer = output.get("answer") if isinstance(output, dict) else None
        grounded = any(c.content_class == ContentClass.FACT and c.fact_refs for c in result.claims)
        if not isinstance(answer, str) or not answer.strip() or not grounded:
            code = (RecommendationErrorCode.GENERATION_PARSE_ERROR
                    if not isinstance(answer, str) else RecommendationErrorCode.NO_GROUNDED_OUTPUT)
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(code, "detail output is not grounded", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        return DetailFollowupResponse(
            build_id=snapshot.build_id,
            ranking_profile_version=snapshot.ranking_profile_version,
            generation_profile_version=gen_profile.version,
            grounded_rules_manifest_hash=gen_profile.grounded_rules_manifest_hash,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            anchor_entity_id=ctx.anchor_entity_id,
            anchor_display_name=detail.display_name,
            answer=answer,
            claims=tuple(result.claims), cited_refs=tuple(result.cited_refs),
            warnings=tuple(w for w in mapped if w.severity != "error"),
        )


__all__ = [
    "ConversationDispatcher", "ConversationValidationError",
    "_validate_context", "_assemble_context", "_detail_support_validator",
    "_merge_stored_context",
]
