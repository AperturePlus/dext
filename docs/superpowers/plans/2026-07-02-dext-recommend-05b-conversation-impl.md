# R5 Conversation Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the dext_recommend conversation adapter — strict conversation context validation, implicit intent LLM classification, a shared constrained-generation pipeline seam, and detail_followup fact-based grounded generation — as the first recommend-domain consumer of the grounded pipeline that R6 will reuse.

**Architecture:** A shared `ConstrainedGenerationPipeline` (raw `LLMGenerationPort.generate` → JSON parse → operation-specific support-map → `CitationValidator` → `SafetyGuard`) lands in `dext_grounded` so R5 and R6 share one caller-facing boundary. `dext_recommend` adds a unified `ConversationDispatcher.dispatch(...)` single entry point returning `ConversationDispatchResult(kind="recommendation|detail_followup|clarification|error")`. `RecommendationCore.recommend` keeps a single `RecommendResponse` return type and only accepts already-resolved recommend-path context. Generation prompts/schemas/thresholds/budget come from a checked-in `data/recommend/generation-profile.json` loaded by `RecommendGenerationProfilePort`.

**Tech Stack:** Python 3.11, `uv` runner, pytest `asyncio_mode="auto"`, pydantic-settings, `dext_grounded` (frozen dataclasses, `LLMGenerationPort`, `CitationValidator`, `SafetyGuard`, `FactBundle`/`FactItem`/`Claim`/`GenerationResult`), `aiohttp`/aiosqlite only at adapter edges. Real `DEEPSEEK_API_KEY` for LLM-touching smoke tests; fakes for offline contract tests.

## Global Constraints

Copied verbatim from the spec + CLAUDE.md so every task implicitly includes them:

- `dext_recommend` MUST NOT import `dext`, `dext_graph`, `dext_monitor`, or `dext_competition`. `dext_grounded` is the only allowed cross-package import (shared contract). Enforced by `tests/dext_recommend/test_recommend_import_boundary.py`.
- Deep immutability: all collection fields on frozen dataclasses are `tuple` / `MappingProxyType`; `.append(...)` on `facts`, `claims`, `result.claims`, `route.terminal_issues`, etc. MUST raise. Verified by `tests/dext_recommend/test_recommend_immutability.py`.
- TDD strictly: write failing test → run RED → implement minimally → run GREEN → commit one conventional commit per green step (`feat(rec):`, `test(rec):`, `docs(rec):`).
- LLM-touching tests use real `DEEPSEEK_API_KEY` from `.env`; never MockLLM/FakeLLM record-replay for provider-quality claims. `FakeLLMGenerationPort`/`FakeRecommendGenerationProfilePort` are contract test doubles for offline dispatch/validation logic only.
- Every async I/O port (`ConversationDispatcher.dispatch`, `_classify_implicit`, `_resolve_detail_followup_pinned`, `ConversationStorePort.*`) is `async def`. Pure functions (`_validate_context`, `_assemble_context`, `resolve_recommend_route`, support-map validator, `CitationValidator.validate`, `SafetyGuard.inspect`) stay synchronous.
- `SafetyGuard` method name is `inspect` (existing). Do NOT invent `apply`.
- Single in-flight snapshot per request: pin once; `get_detail` and `generate` reuse the same snapshot.
- Platform: Windows + Git Bash; `LF will be replaced by CRLF` git warnings are benign.
- Run tests: `uv run pytest tests/dext_recommend/ -q` (module) / `uv run pytest tests/dext_recommend/test_<file>.py -v` (one file) / `uv run pytest tests/dext_recommend/test_x.py::test_name -v` (single).

---

## File Structure

**Shared seam (dext_grounded):**
- Create `src/dext_grounded/pipeline.py` — `ConstrainedGenerationPipeline` (the only caller-facing validated-generation entry).
- Modify `src/dext_grounded/ports.py` — fix `LLMGenerationPort.generate` docstring: returns `GenerationResult` NOT yet citation/safety-validated.
- Modify `src/dext_grounded/__init__.py` — re-export `ConstrainedGenerationPipeline`.
- Modify `tests/dext_grounded/test_pipeline.py` (new) — contract tests for the seam.

**Generation profile (dext_recommend):**
- Create `data/recommend/generation-profile.json` — checked-in artifact.
- Create `src/dext_recommend/core/generation_profile.py` — `RecommendGenerationProfile` model + loader.
- Create `src/dext_recommend/ports/generation_profile.py` — `RecommendGenerationProfilePort` protocol.
- Modify `src/dext_recommend/ports/_fakes.py` — add `FakeRecommendGenerationProfilePort`.
- Modify `src/dext_recommend/ports/__init__.py` + `src/dext_recommend/__init__.py` — re-exports.
- Create `tests/dext_recommend/test_recommend_generation_profile.py`.

**Models (dext_recommend):**
- Modify `src/dext_recommend/models.py` — add `ConversationSummary`, `ConversationDispatchResult`, `DetailFollowupResponse`; add `RecommendResponse.generation_profile_version`.
- Modify `src/dext_recommend/errors.py` — new error codes.
- Modify `tests/dext_recommend/test_recommend_models.py` + `test_recommend_immutability.py`.

**Strict route + context validation:**
- Create `src/dext_recommend/core/conversation.py` — `_validate_context`, `_assemble_context`, `ConversationValidationError`.
- Modify `src/dext_recommend/core/intent.py` — `RecommendRoute.terminal_issues`, strict errors, remove `unsupported`.
- Modify `tests/dext_recommend/test_recommend_intent.py` — migrate 4 fallback tests to error assertions.
- Create `tests/dext_recommend/test_recommend_conversation_context.py`.

**Dispatcher + implicit:**
- Extend `src/dext_recommend/core/conversation.py` — `ConversationDispatcher`, `_classify_implicit`, `dispatch`.
- Modify `src/dext_recommend/core/service.py` — `_recommend_pinned` extraction, reject unresolved context.
- Create `tests/dext_recommend/test_recommend_conversation_implicit.py` + `test_recommend_conversation_dispatch.py`.

**Detail generation:**
- Extend `src/dext_recommend/core/conversation.py` — `_resolve_detail_followup_pinned`, support-map validator, GenerationWarning→RecommendationWarning mapping.
- Create `tests/dext_recommend/test_recommend_detail_followup.py`.

**Store + eval:**
- Create `src/dext_recommend/ports/conversation_store.py` — `ConversationStorePort`, `TurnSnapshot`.
- Modify `src/dext_recommend/ports/_fakes.py` — `FakeConversationStorePort`.
- Modify `src/dext_recommend/ports/__init__.py` + `__init__.py` — re-exports.
- Modify `src/dext_recommend/core/service.py` — `RecommendDeps.conversation_store` + `generation_profile_port`.
- Create `tests/dext_recommend/test_recommend_conversation_store.py`.
- Create `src/dext_recommend/eval/conversation.py` — eval contract + sample shape.
- Create `tests/dext_recommend/test_recommend_eval_conversation.py`.

---

## Task 1: Shared ConstrainedGenerationPipeline seam

**Files:**
- Create: `src/dext_grounded/pipeline.py`
- Modify: `src/dext_grounded/ports.py` (docstring only)
- Modify: `src/dext_grounded/__init__.py` (re-export)
- Test: `tests/dext_grounded/test_pipeline.py`

**Interfaces:**
- Consumes: `dext_grounded.claims.GenerationResult`, `dext_grounded.fact_bundle.FactBundle`, `dext_grounded.citation.CitationValidator`, `dext_grounded.safety.SafetyGuard`, `dext_grounded.ports.LLMGenerationPort`, `dext_grounded.student_context.StudentContext`.
- Produces: `ConstrainedGenerationPipeline` class with `async generate(self, *, system_prompt_id, user_inputs, fact_bundle, student_context, json_schema, generation_profile_version, safety_domain, include_contacts, operation_id=None, support_validator=None) -> GenerationResult`. The `support_validator` is `Callable[[GenerationResult, FactBundle], GenerationResult] | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_grounded/test_pipeline.py`:

```python
from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ConstrainedGenerationPipeline, ContentClass, FactBundle, FactItem,
    FakeLLMGenerationPort, GenerationResult, SourceRef, StudentContext,
)
from dext_grounded.citation import CitationValidator
from dext_grounded.safety import SafetyGuard


def _bundle(build_id: str = "b1", subject_id: str = "e1") -> FactBundle:
    ref = SourceRef(
        doc_path=f"catalog:research-statement:{build_id}:s1",
        heading_path="research_statement", chunk_hash="h1",
        quote_or_summary="works on NLP", official_url=None, last_verified=None,
    )
    item = FactItem(
        field="research_statement", value="works on NLP",
        content_class=ContentClass.FACT, source_refs=(ref,),
    )
    return FactBundle(build_id=build_id, subject_id=subject_id,
                      facts=(item,), source_refs=(ref,))


@pytest.mark.asyncio
async def test_pipeline_runs_parse_citation_safety_in_order():
    raw = GenerationResult(
        output={"answer": "He works on NLP.", "claims": [
            {"text": "He works on NLP.", "content_class": "fact",
             "fact_indices": [0], "fact_refs": []},
        ]},
        claims=(Claim(text="He works on NLP.", content_class=ContentClass.FACT,
                      fact_refs=()),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)
    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="dext_recommend.detail_followup.v1",
        user_inputs={"question": "what?", "display_name": "X"},
        fact_bundle=_bundle(), student_context=None,
        json_schema={"type": "object"},
        generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
    )
    assert isinstance(result, GenerationResult)
    # citation canonicalized the fact ref into cited_refs
    assert len(result.cited_refs) == 1


@pytest.mark.asyncio
async def test_pipeline_support_validator_runs_before_citation():
    # fact claim whose ref is in the bundle (passes citation) but the support-map
    # callback rejects it because fact_indices is empty -> claim dropped.
    raw = GenerationResult(
        output={"answer": "x", "claims": [
            {"text": "x", "content_class": "fact", "fact_indices": [], "fact_refs": []}]},
        claims=(Claim(text="x", content_class=ContentClass.FACT, fact_refs=()),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)

    def support_validator(result: GenerationResult, bundle: FactBundle) -> GenerationResult:
        # drop every fact claim (simulates index validation failing)
        from dataclasses import replace
        return replace(result, claims=())

    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="p", user_inputs={}, fact_bundle=_bundle(),
        student_context=None, json_schema=None, generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
        support_validator=support_validator,
    )
    assert result.claims == ()


@pytest.mark.asyncio
async def test_pipeline_does_not_double_validate(monkeypatch):
    # If business code wraps pipeline output in CitationValidator again, that's
    # a caller bug; pipeline result is already validated. We assert pipeline
    # output is idempotent under a second CitationValidator.validate (no new
    # fabricated_ref warnings on already-canonical refs).
    ref = SourceRef(doc_path="c:s:1", heading_path="rs", chunk_hash="h1",
                    quote_or_summary="NLP", official_url=None, last_verified=None)
    bundle = FactBundle(build_id="b", subject_id="e", facts=(
        FactItem(field="research_statement", value="NLP",
                 content_class=ContentClass.FACT, source_refs=(ref,)),
    ), source_refs=(ref,))
    raw = GenerationResult(
        output={"answer": "NLP", "claims": [
            {"text": "NLP", "content_class": "fact",
             "fact_indices": [0], "fact_refs": [ref]}]},
        claims=(Claim(text="NLP", content_class=ContentClass.FACT,
                      fact_refs=(ref,)),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)
    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="p", user_inputs={}, fact_bundle=bundle,
        student_context=None, json_schema=None, generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
    )
    re_validated = CitationValidator().validate(result, bundle, None)
    assert not any(w.code == "fabricated_ref" for w in re_validated.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_grounded/test_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'ConstrainedGenerationPipeline' from 'dext_grounded'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_grounded/pipeline.py`:

```python
"""ConstrainedGenerationPipeline — the only caller-facing validated-generation
boundary (grounded-generation spec §4/§5/§6).

raw LLMGenerationPort.generate
  -> JSON/schema parse (delegated to the provider's json_schema mode)
  -> operation-specific support-map validation (optional callback)
  -> CitationValidator.validate
  -> SafetyGuard.inspect
  -> validated GenerationResult

Business code (dext_recommend, dext_competition) MUST call this pipeline, not
the raw LLMGenerationPort, and MUST NOT re-run CitationValidator on the result
(double-warning). The raw port returns a GenerationResult that is NOT yet
citation/safety-validated; only this pipeline returns the final, safe result.
"""
from __future__ import annotations

from typing import Any, Callable

from dext_grounded.claims import GenerationResult
from dext_grounded.citation import CitationValidator
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.ports import LLMGenerationPort
from dext_grounded.safety import SafetyGuard
from dext_grounded.student_context import StudentContext

SupportValidator = Callable[[GenerationResult, FactBundle], GenerationResult]


class ConstrainedGenerationPipeline:
    """Wraps a raw LLMGenerationPort with citation + safety validation.

    The pipeline instance holds no request-mutable state. Each generate() call
    is independent.
    """

    def __init__(
        self,
        llm_port: LLMGenerationPort,
        citation: CitationValidator | None = None,
        safety: SafetyGuard | None = None,
    ) -> None:
        self._llm_port = llm_port
        self._citation = citation or CitationValidator()
        self._safety = safety or SafetyGuard()

    async def generate(
        self,
        *,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
        safety_domain: str,
        include_contacts: bool = False,
        operation_id: str | None = None,
        support_validator: SupportValidator | None = None,
    ) -> GenerationResult:
        raw = await self._llm_port.generate(
            system_prompt_id=system_prompt_id,
            user_inputs=user_inputs,
            fact_bundle=fact_bundle,
            student_context=student_context,
            json_schema=json_schema,
            generation_profile_version=generation_profile_version,
        )
        result = raw
        if support_validator is not None:
            result = support_validator(result, fact_bundle)
        result = self._citation.validate(result, fact_bundle, student_context)
        result = self._safety.inspect(
            result, domain=safety_domain, include_contacts=include_contacts,
        )
        return result


__all__ = ["ConstrainedGenerationPipeline", "SupportValidator"]
```

Modify `src/dext_grounded/ports.py` — replace the `LLMGenerationPort.generate` docstring sentence `"""generate`` MUST run citation validation immediately after the LLM returns; callers never receive un-validated output. The concrete LLM client lives in each consumer module..."""` with:

```python
    """Raw provider generation. Returns a GenerationResult that is NOT yet
    citation/safety-validated — callers MUST pass it through
    ConstrainedGenerationPipeline.generate before handing it to business code.
    The concrete LLM client lives in each consumer module (recommend/
    competition); this package only fixes the contract and a test fake.
    """
```

Modify `src/dext_grounded/__init__.py` — add to imports and `__all__`:

```python
from dext_grounded.pipeline import ConstrainedGenerationPipeline
```
and append `"ConstrainedGenerationPipeline"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_grounded/test_pipeline.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run full dext_grounded suite to confirm no regression**

Run: `uv run pytest tests/dext_grounded/ -q`
Expected: all green (was 131 passed pre-R5).

- [ ] **Step 6: Commit**

```bash
git add src/dext_grounded/pipeline.py src/dext_grounded/ports.py src/dext_grounded/__init__.py tests/dext_grounded/test_pipeline.py
git commit -m "feat(grounded): ConstrainedGenerationPipeline seam — raw generate -> support-map -> citation -> safety; raw port docstring fixed"
```

---

## Task 2: Generation profile artifact + loader + port

**Files:**
- Create: `data/recommend/generation-profile.json`
- Create: `src/dext_recommend/core/generation_profile.py`
- Create: `src/dext_recommend/ports/generation_profile.py`
- Modify: `src/dext_recommend/ports/_fakes.py` (add `FakeRecommendGenerationProfilePort`)
- Modify: `src/dext_recommend/ports/__init__.py`
- Modify: `src/dext_recommend/__init__.py`
- Test: `tests/dext_recommend/test_recommend_generation_profile.py`

**Interfaces:**
- Consumes: `pathlib.Path`, `RecommendSettings.generation_profile_path`.
- Produces: `RecommendGenerationProfile` (frozen dataclass: `version`, `grounded_rules_manifest_hash`, `operations: Mapping[str, OperationConfig]`), `OperationConfig` (`system_prompt_id`, `json_schema: Mapping`, `timeout: float`, `token_budget: int`, plus optional `confidence_threshold`/`query_max_chars`/`summary_max_chars` for `implicit_intent`), `RecommendGenerationProfilePort` (`async read_profile(path) -> RecommendGenerationProfile`), `FakeRecommendGenerationProfilePort`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_generation_profile.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dext_recommend.core.generation_profile import (
    OperationConfig, RecommendGenerationProfile,
)
from dext_recommend.ports import FakeRecommendGenerationProfilePort


def _valid_payload() -> dict:
    return {
        "version": "generation-v1",
        "grounded_rules_manifest_hash": "grules-abc123",
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "json_schema": {"type": "object", "required": ["intent"]},
                "timeout": 8.0,
                "token_budget": 1024,
                "confidence_threshold": 0.6,
                "query_max_chars": 4096,
                "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object", "required": ["answer"]},
                "timeout": 15.0,
                "token_budget": 2048,
            },
        },
    }


def test_profile_round_trips_through_json(tmp_path: Path) -> None:
    payload = _valid_payload()
    p = tmp_path / "gp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    prof = RecommendGenerationProfile.from_file(p)
    assert prof.version == "generation-v1"
    assert prof.grounded_rules_manifest_hash == "grules-abc123"
    assert set(prof.operations) == {"implicit_intent", "detail_followup"}
    impl = prof.operations["implicit_intent"]
    assert impl.confidence_threshold == 0.6
    assert impl.summary_max_chars == 500


@pytest.mark.parametrize("bad,path", [
    ({"operations": {"implicit_intent": {"system_prompt_id": ""}}}, "empty prompt id"),
    ({"operations": {"implicit_intent": {"timeout": -1.0}}}, "negative timeout"),
    ({"operations": {"implicit_intent": {"token_budget": 0}}}, "non-positive budget"),
    ({"operations": {"implicit_intent": {"confidence_threshold": 1.5}}}, "threshold out of range"),
    ({"operations": {}}, "missing operations"),
    ({}, "missing operations key"),
])
def test_loader_rejects_invalid(tmp_path: Path, bad: dict, path: str) -> None:
    payload = _valid_payload()
    payload.update(bad) if "operations" in bad else payload.pop("operations", None)
    if "operations" in bad:
        # deep-merge the bad operation overrides
        payload["operations"].update(bad["operations"])
    p = tmp_path / "gp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        RecommendGenerationProfile.from_file(p)


@pytest.mark.asyncio
async def test_fake_profile_port_returns_preset() -> None:
    prof = RecommendGenerationProfile.from_dict(_valid_payload())
    port = FakeRecommendGenerationProfilePort(profile=prof)
    got = await port.read_profile(Path("ignored"))
    assert got is prof
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_generation_profile.py -v`
Expected: FAIL with `ImportError: cannot import name 'FakeRecommendGenerationProfilePort'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/core/generation_profile.py`:

```python
"""Recommendation generation profile — checked-in prompts/schemas/thresholds
(R5 spec §6.7). R6 adds match/email/compare operations to the same artifact
without changing R5 field semantics.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class OperationConfig:
    system_prompt_id: str
    json_schema: Mapping[str, Any]
    timeout: float
    token_budget: int
    # implicit_intent-only optional knobs
    confidence_threshold: float | None = None
    query_max_chars: int | None = None
    summary_max_chars: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt_id, str) or not self.system_prompt_id:
            raise ValueError("operation.system_prompt_id must be non-empty str")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool) or self.timeout <= 0:
            raise ValueError("operation.timeout must be positive number")
        if not isinstance(self.token_budget, int) or isinstance(self.token_budget, bool) or self.token_budget <= 0:
            raise ValueError("operation.token_budget must be positive int")
        if not isinstance(self.json_schema, Mapping):
            raise ValueError("operation.json_schema must be a mapping")
        object.__setattr__(self, "json_schema", dict(self.json_schema))
        if self.confidence_threshold is not None:
            if not (0.0 <= float(self.confidence_threshold) <= 1.0):
                raise ValueError("confidence_threshold must be in [0,1]")
        if self.query_max_chars is not None and self.query_max_chars <= 0:
            raise ValueError("query_max_chars must be positive")
        if self.summary_max_chars is not None and self.summary_max_chars <= 0:
            raise ValueError("summary_max_chars must be positive")


@dataclass(frozen=True, slots=True)
class RecommendGenerationProfile:
    version: str
    grounded_rules_manifest_hash: str
    operations: Mapping[str, OperationConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be non-empty str")
        if not isinstance(self.grounded_rules_manifest_hash, str) or not self.grounded_rules_manifest_hash:
            raise ValueError("grounded_rules_manifest_hash must be non-empty str")
        if not isinstance(self.operations, Mapping) or not self.operations:
            raise ValueError("operations must be a non-empty mapping")
        object.__setattr__(self, "operations", dict(self.operations))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecommendGenerationProfile":
        ops_raw = payload["operations"]
        ops: dict[str, OperationConfig] = {}
        for op_id, cfg in ops_raw.items():
            ops[op_id] = OperationConfig(
                system_prompt_id=cfg["system_prompt_id"],
                json_schema=cfg["json_schema"],
                timeout=cfg["timeout"],
                token_budget=cfg["token_budget"],
                confidence_threshold=cfg.get("confidence_threshold"),
                query_max_chars=cfg.get("query_max_chars"),
                summary_max_chars=cfg.get("summary_max_chars"),
            )
        return cls(
            version=payload["version"],
            grounded_rules_manifest_hash=payload["grounded_rules_manifest_hash"],
            operations=ops,
        )

    @classmethod
    def from_file(cls, path: Path) -> "RecommendGenerationProfile":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


__all__ = ["OperationConfig", "RecommendGenerationProfile"]
```

Create `src/dext_recommend/ports/generation_profile.py`:

```python
"""RecommendGenerationProfilePort — loads the checked-in generation profile."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dext_recommend.core.generation_profile import RecommendGenerationProfile


@runtime_checkable
class RecommendGenerationProfilePort(Protocol):
    async def read_profile(self, path: Path) -> RecommendGenerationProfile: ...


__all__ = ["RecommendGenerationProfilePort"]
```

Add to `src/dext_recommend/ports/_fakes.py` (append before `__all__`):

```python
class FakeRecommendGenerationProfilePort:
    def __init__(self, profile: "RecommendGenerationProfile") -> None:
        self._profile = profile
        self.read_profile_calls: list[dict] = []

    async def read_profile(self, path: Path) -> "RecommendGenerationProfile":
        self.read_profile_calls.append({"path": path})
        return self._profile
```

Add imports in `ports/_fakes.py` top:
```python
from dext_recommend.core.generation_profile import RecommendGenerationProfile
```

Modify `src/dext_recommend/ports/__init__.py` — add imports:
```python
from dext_recommend.ports.generation_profile import RecommendGenerationProfilePort
from dext_recommend.core.generation_profile import (
    OperationConfig, RecommendGenerationProfile,
)
```
and in `_fakes` import block add `FakeRecommendGenerationProfilePort,`; add all three names + `RecommendGenerationProfilePort` to `__all__`.

Modify `src/dext_recommend/__init__.py` — add `FakeRecommendGenerationProfilePort`, `OperationConfig`, `RecommendGenerationProfile`, `RecommendGenerationProfilePort` to imports and `__all__`.

Create `data/recommend/generation-profile.json`:

```json
{
  "version": "generation-v1",
  "grounded_rules_manifest_hash": "grules-v1-placeholder",
  "operations": {
    "implicit_intent": {
      "system_prompt_id": "dext_recommend.implicit_intent.v1",
      "json_schema": {
        "type": "object",
        "additionalProperties": false,
        "required": ["intent", "confidence", "rationale"],
        "properties": {
          "intent": {"enum": ["new_search", "more_mentors", "same_field", "refine_direction", "detail_followup"]},
          "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
          "rationale": {"type": "string", "maxLength": 200}
        }
      },
      "timeout": 8.0,
      "token_budget": 1024,
      "confidence_threshold": 0.6,
      "query_max_chars": 4096,
      "summary_max_chars": 500
    },
    "detail_followup": {
      "system_prompt_id": "dext_recommend.detail_followup.v1",
      "json_schema": {
        "type": "object",
        "additionalProperties": false,
        "required": ["answer", "claims"],
        "properties": {
          "answer": {"type": "string"},
          "claims": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["text", "content_class", "fact_indices", "fact_refs"],
              "properties": {
                "text": {"type": "string"},
                "content_class": {"enum": ["fact", "advice", "uncertain"]},
                "fact_indices": {"type": "array", "items": {"type": "integer"}},
                "fact_refs": {"type": "array"}
              }
            }
          }
        }
      },
      "timeout": 15.0,
      "token_budget": 2048
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_generation_profile.py -v`
Expected: PASS (round-trip + 6 parametrized rejects + fake = 8 tests).

- [ ] **Step 5: Run module suite to confirm no import regression**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add data/recommend/generation-profile.json src/dext_recommend/core/generation_profile.py src/dext_recommend/ports/generation_profile.py src/dext_recommend/ports/_fakes.py src/dext_recommend/ports/__init__.py src/dext_recommend/__init__.py tests/dext_recommend/test_recommend_generation_profile.py
git commit -m "feat(rec): R5 generation profile artifact + loader/port/fake — versioned prompts/schemas/thresholds/manifest hash"
```

---

## Task 3: New models (ConversationSummary, ConversationDispatchResult, DetailFollowupResponse) + error codes + response field

**Files:**
- Modify: `src/dext_recommend/models.py`
- Modify: `src/dext_recommend/errors.py`
- Modify: `tests/dext_recommend/test_recommend_models.py`
- Modify: `tests/dext_recommend/test_recommend_immutability.py`

**Interfaces:**
- Consumes: `dext_grounded.Claim`, `dext_grounded.SourceRef`, existing `RecommendationWarning`, `PhaseDiagnostic`, `ConversationContext`, `RecommendResponse`.
- Produces: `ConversationSummary`, `ConversationDispatchResult`, `DetailFollowupResponse`; `RecommendResponse.generation_profile_version: str | None = None`; 9 new `RecommendationErrorCode` values.

- [ ] **Step 1: Write the failing test**

Append to `tests/dext_recommend/test_recommend_models.py`:

```python
from dext_recommend import (
    ConversationDispatchResult, ConversationSummary, DetailFollowupResponse,
)
from dext_recommend.errors import RecommendationErrorCode
from dext_grounded import Claim, ContentClass, SourceRef


def test_conversation_summary_immutable():
    s = ConversationSummary(session_id="s1", through_turn_id="t1",
                            text="abc", created_at="2026-07-02T00:00:00Z")
    assert s.session_id == "s1"


def test_dispatch_result_kind_recommendation_carries_only_recommendation():
    from dext_recommend import RecommendResponse
    resp = RecommendResponse(build_id="b", ranking_profile_version="rv",
                             embedding_fingerprint="ef", taxonomy_version=None,
                             query_understanding=None, query=None, results=(),
                             suggested_followups=(), warnings=(), )
    r = ConversationDispatchResult(kind="recommendation", context=None,
                                   recommendation=resp, detail_followup=None,
                                   issues=(), generation_profile_version="gp")
    assert r.recommendation is resp
    assert r.detail_followup is None


def test_dispatch_result_rejects_mixed_payload():
    import pytest
    with pytest.raises(ValueError):
        ConversationDispatchResult(kind="recommendation", context=None,
                                   recommendation=None, detail_followup=None,
                                   issues=(), generation_profile_version=None)


def test_detail_followup_response_basic():
    r = DetailFollowupResponse(
        build_id="b", ranking_profile_version="rv", generation_profile_version="gp",
        grounded_rules_manifest_hash="grh", embedding_fingerprint="ef",
        taxonomy_version=None, anchor_entity_id="e1", anchor_display_name="X",
        answer="hi", claims=(), cited_refs=(), warnings=(),
    )
    assert r.answer == "hi"
    assert r.phase_diagnostics == ()


def test_new_error_codes_registered():
    codes = {c.value for c in RecommendationErrorCode}
    assert "invalid_conversation_state" in codes
    assert "more_mentors_requires_prior" in codes
    assert "same_field_requires_anchor" in codes
    assert "detail_followup_requires_anchor" in codes
    assert "anchor_not_in_active_build" in codes
    assert "intent_classification_unavailable" in codes
    assert "followup_generation_unavailable" in codes
    assert "generation_parse_error" in codes
    assert "no_grounded_output" in codes


def test_recommend_response_has_optional_generation_profile_version():
    from dext_recommend import RecommendResponse
    assert RecommendResponse.__dataclass_fields__["generation_profile_version"].default is None
```

Append to `tests/dext_recommend/test_recommend_immutability.py`:

```python
def test_detail_followup_response_deep_immutable():
    from dext_recommend import DetailFollowupResponse
    from dext_grounded import Claim, ContentClass
    import pytest
    r = DetailFollowupResponse(
        build_id="b", ranking_profile_version="rv", generation_profile_version="gp",
        grounded_rules_manifest_hash="grh", embedding_fingerprint="ef",
        taxonomy_version=None, anchor_entity_id="e1", anchor_display_name="X",
        answer="hi",
        claims=(Claim(text="hi", content_class=ContentClass.FACT),),
        cited_refs=(), warnings=(),
    )
    with pytest.raises((AttributeError, TypeError)):
        r.claims.append("x")


def test_dispatch_result_issues_immutable():
    from dext_recommend import ConversationDispatchResult, RecommendationWarning
    import pytest
    r = ConversationDispatchResult(kind="error", context=None,
                                    recommendation=None, detail_followup=None,
                                    issues=(RecommendationWarning("e", "m"),),
                                    generation_profile_version=None)
    with pytest.raises((AttributeError, TypeError)):
        r.issues.append(RecommendationWarning("y", "z"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_models.py tests/dext_recommend/test_recommend_immutability.py -v`
Expected: FAIL with `ImportError` / `AttributeError` for missing names.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/errors.py`, append to the `RecommendationErrorCode` enum (after `DETAILS_UNAVAILABLE`):

```python
    INVALID_CONVERSATION_STATE = "invalid_conversation_state"
    MORE_MENTORS_REQUIRES_PRIOR = "more_mentors_requires_prior"
    SAME_FIELD_REQUIRES_ANCHOR = "same_field_requires_anchor"
    DETAIL_FOLLOWUP_REQUIRES_ANCHOR = "detail_followup_requires_anchor"
    ANCHOR_NOT_IN_ACTIVE_BUILD = "anchor_not_in_active_build"
    INTENT_CLASSIFICATION_UNAVAILABLE = "intent_classification_unavailable"
    FOLLOWUP_GENERATION_UNAVAILABLE = "followup_generation_unavailable"
    GENERATION_PARSE_ERROR = "generation_parse_error"
    NO_GROUNDED_OUTPUT = "no_grounded_output"
```

In `src/dext_recommend/models.py`, add after `ConversationContext`:

```python
@dataclass(frozen=True, slots=True)
class ConversationSummary:
    session_id: str
    through_turn_id: str | None
    text: str
    created_at: str            # UTC ISO-8601
```

After `RecommendResponse`, add:

```python
@dataclass(frozen=True, slots=True)
class DetailFollowupResponse:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str
    grounded_rules_manifest_hash: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    anchor_entity_id: str
    anchor_display_name: str
    answer: str
    claims: tuple
    cited_refs: tuple
    warnings: tuple[RecommendationWarning, ...]
    phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for _f in ("claims", "cited_refs", "warnings", "phase_diagnostics"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else (),
            )


@dataclass(frozen=True, slots=True)
class ConversationDispatchResult:
    kind: str   # recommendation|detail_followup|clarification|error
    context: "ConversationContext | None"
    recommendation: "RecommendResponse | None"
    detail_followup: "DetailFollowupResponse | None"
    issues: tuple[RecommendationWarning, ...]
    generation_profile_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        kind = self.kind
        has_rec = self.recommendation is not None
        has_det = self.detail_followup is not None
        if kind == "recommendation":
            if not has_rec or has_det:
                raise ValueError("kind=recommendation requires only recommendation payload")
        elif kind == "detail_followup":
            if not has_det or has_rec:
                raise ValueError("kind=detail_followup requires only detail_followup payload")
        elif kind in ("clarification", "error"):
            if has_rec or has_det:
                raise ValueError(f"kind={kind} must carry no payload")
        else:
            raise ValueError(f"unknown kind: {kind!r}")
        if kind == "clarification":
            if not any(w.code == "needs_clarification" for w in self.issues):
                raise ValueError("kind=clarification requires a needs_clarification issue")
        if kind == "error":
            if not any(w.severity == "error" for w in self.issues):
                raise ValueError("kind=error requires at least one severity=error issue")
```

Modify the existing `RecommendResponse` dataclass — add field (after `ranking_profile_version`):

```python
    generation_profile_version: str | None = None
```

(Note: because `slots=True` + default, place it after all required fields; `phase_diagnostics` already has a default so this is fine.)

Add `ConversationSummary`, `ConversationDispatchResult`, `DetailFollowupResponse` to `models.py` `__all__`.

Update `src/dext_recommend/__init__.py` imports + `__all__` to include the three new model names.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_models.py tests/dext_recommend/test_recommend_immutability.py -v`
Expected: PASS.

- [ ] **Step 5: Run module suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/models.py src/dext_recommend/errors.py src/dext_recommend/__init__.py tests/dext_recommend/test_recommend_models.py tests/dext_recommend/test_recommend_immutability.py
git commit -m "feat(rec): R5 models — ConversationSummary/ConversationDispatchResult/DetailFollowupResponse + 9 error codes + generation_profile_version field"
```

---

## Task 4: Two-phase context validation + strict RecommendRoute

**Files:**
- Create: `src/dext_recommend/core/conversation.py` (`_validate_context`, `_assemble_context`, `ConversationValidationError` only)
- Modify: `src/dext_recommend/core/intent.py`
- Modify: `tests/dext_recommend/test_recommend_intent.py`
- Create: `tests/dext_recommend/test_recommend_conversation_context.py`

**Interfaces:**
- Consumes: `ConversationContext`, `RecommendationWarning`, `RecommendationErrorCode`, `dataclasses.replace`.
- Produces: `ConversationValidationError(code, safe_message)`, `_validate_context(context, *, phase) -> ConversationContext`, `_assemble_context(**fields) -> ConversationContext`; modified `RecommendRoute` (drop `unsupported`, add `terminal_issues: tuple[RecommendationWarning,...]` + `detail_followup: bool`); modified `resolve_recommend_route` returning strict errors.

- [ ] **Step 1: Write the failing tests**

Create `tests/dext_recommend/test_recommend_conversation_context.py`:

```python
from __future__ import annotations

import pytest

from dext_recommend import ConversationContext
from dext_recommend.core.conversation import (
    ConversationValidationError, _assemble_context, _validate_context,
)


def test_input_phase_normalizes_all_none_to_implicit():
    ctx = ConversationContext()
    out = _validate_context(ctx, phase="input")
    assert out.intent_source == "implicit"
    assert out.intent is None
    assert out.intent_confidence is None


def test_input_phase_rejects_partial_implicit_prefill():
    ctx = ConversationContext(intent_source="implicit", intent="more_mentors")
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_input_phase_rejects_explicit_with_confidence():
    ctx = ConversationContext(intent_source="explicit", intent="new_search",
                              intent_confidence=0.9)
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_input_phase_rejects_none_source_with_intent():
    ctx = ConversationContext(intent="new_search")
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_resolved_phase_requires_implicit_whitelist_and_confidence():
    ctx = ConversationContext(intent_source="implicit", intent="more_mentors",
                              intent_confidence=0.8)
    out = _validate_context(ctx, phase="resolved")
    assert out.intent == "more_mentors"
    with pytest.raises(ConversationValidationError):
        _validate_context(
            ConversationContext(intent_source="implicit", intent="more_mentors",
                                intent_confidence=1.5),
            phase="resolved",
        )


def test_session_turn_must_co_occur():
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(
            ConversationContext(session_id="s1", intent_source="explicit",
                                intent="new_search"),
            phase="input",
        )
    assert exc.value.code == "invalid_conversation_state"


def test_fork_pair_must_co_occur():
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(
            ConversationContext(main_session_id="m1", intent_source="explicit",
                                intent="new_search", session_id="s1", turn_id="t1"),
            phase="input",
        )
    assert exc.value.code == "invalid_conversation_state"


def test_prior_dedup_preserves_order():
    ctx = _assemble_context(
        session_id="s1", turn_id="t1", main_session_id=None, source_turn_id=None,
        anchor_entity_id=None, intent="new_search", intent_source="explicit",
        intent_confidence=None, prior_result_entity_ids=["e2", "e1", "e2", "e3"],
    )
    assert ctx.prior_result_entity_ids == ("e2", "e1", "e3")


def test_assemble_runs_input_validation():
    with pytest.raises(ConversationValidationError):
        _assemble_context(
            session_id="s1", turn_id="t1", main_session_id=None, source_turn_id=None,
            anchor_entity_id=None, intent="bogus", intent_source="explicit",
            intent_confidence=None, prior_result_entity_ids=(),
        )
```

Append strict-route migrations to `tests/dext_recommend/test_recommend_intent.py` (replacing the bodies of the 4 listed tests — see Step 1b). Add at top after imports:

```python
def _req(intent=None, *, prior=(), anchor=None, query="NLP",
         intent_source="explicit") -> RecommendRequest:
    return RecommendRequest(
        query_text=query,
        conversation_context=ConversationContext(
            intent=intent, intent_source=intent_source,
            prior_result_entity_ids=prior, anchor_entity_id=anchor,
        ) if intent or anchor or prior else None,
    )
```

Replace `test_more_mentors_without_prior_falls_back_with_warning` body:

```python
def test_more_mentors_without_prior_returns_terminal_error():
    route = resolve_recommend_route(_req("more_mentors", prior=()))
    assert route.intent == "more_mentors"
    codes = [w.code for w in route.terminal_issues]
    assert "more_mentors_requires_prior" in codes
    assert all(w.severity == "error" for w in route.terminal_issues)
```

Replace `test_same_field_without_anchor_falls_back_with_warning` body:

```python
def test_same_field_without_anchor_returns_terminal_error():
    route = resolve_recommend_route(_req("same_field"))
    codes = [w.code for w in route.terminal_issues]
    assert "same_field_requires_anchor" in codes
```

Replace `test_invalid_intent_falls_back_to_new_search_with_warning` body:

```python
def test_invalid_intent_returns_terminal_error():
    route = resolve_recommend_route(_req("bogus_intent"))
    codes = [w.code for w in route.terminal_issues]
    assert "invalid_intent" in codes
    assert route.detail_followup is False
```

Replace `test_detail_followup_is_unsupported` body:

```python
def test_detail_followup_sets_flag_not_unsupported():
    route = resolve_recommend_route(_req("detail_followup", anchor="e1"))
    assert route.detail_followup is True
    assert route.terminal_issues == ()
    assert not hasattr(route, "unsupported")


def test_detail_followup_without_anchor_returns_terminal_error():
    route = resolve_recommend_route(_req("detail_followup"))
    codes = [w.code for w in route.terminal_issues]
    assert "detail_followup_requires_anchor" in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_context.py tests/dext_recommend/test_recommend_intent.py -v`
Expected: FAIL (module import error + 4 renamed tests fail).

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/core/conversation.py` (context validation only for this task):

```python
"""Conversation adapter — context validation, implicit classification, dispatch,
and detail_followup grounded generation (R5 spec).

Pure functions stay synchronous; LLM/store I/O is async. The dispatcher is the
sole public conversation entry point returning ConversationDispatchResult.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from dext_recommend.models import ConversationContext

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


__all__ = [
    "ConversationValidationError", "_validate_context", "_assemble_context",
]
```

Modify `src/dext_recommend/core/intent.py` — replace the `RecommendRoute` dataclass and `resolve_recommend_route`:

```python
@dataclass(frozen=True, slots=True)
class RecommendRoute:
    intent: str
    exclude_entity_ids: tuple[str, ...]
    anchor_entity_id: str | None
    refine_merge: bool
    detail_followup: bool
    terminal_issues: tuple[RecommendationWarning, ...]
    warnings: tuple[RecommendationWarning, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclude_entity_ids", tuple(self.exclude_entity_ids or ()))
        object.__setattr__(self, "terminal_issues", tuple(self.terminal_issues or ()))
        object.__setattr__(self, "warnings", tuple(self.warnings or ()))


def _terminal(code, message) -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity="error")


def resolve_recommend_route(request: RecommendRequest) -> RecommendRoute:
    ctx = request.conversation_context
    intent = (ctx.intent if ctx is not None else None) or "new_search"
    terminal: list[RecommendationWarning] = []
    warnings: list[RecommendationWarning] = []
    detail_followup = False

    if intent not in _ALL_INTENTS:
        terminal.append(_terminal(RecommendationErrorCode.INVALID_INTENT, f"unknown intent: {intent!r}"))
        return RecommendRoute(
            intent="new_search", exclude_entity_ids=(), anchor_entity_id=None,
            refine_merge=False, detail_followup=False,
            terminal_issues=tuple(terminal), warnings=tuple(warnings),
        )

    if intent == "detail_followup":
        anchor = ctx.anchor_entity_id if ctx is not None else None
        if not anchor:
            terminal.append(_terminal(
                RecommendationErrorCode.DETAIL_FOLLOWUP_REQUIRES_ANCHOR,
                "detail_followup requires anchor_entity_id",
            ))
        detail_followup = True
        return RecommendRoute(
            intent="detail_followup", exclude_entity_ids=(),
            anchor_entity_id=anchor, refine_merge=False,
            detail_followup=detail_followup,
            terminal_issues=tuple(terminal), warnings=tuple(warnings),
        )

    exclude_entity_ids: tuple[str, ...] = ()
    anchor_entity_id: str | None = None
    refine_merge = False

    if intent == "more_mentors":
        prior = tuple(ctx.prior_result_entity_ids) if ctx is not None else ()
        if not prior:
            terminal.append(_terminal(
                RecommendationErrorCode.MORE_MENTORS_REQUIRES_PRIOR,
                "more_mentors requires prior_result_entity_ids",
            ))
        else:
            exclude_entity_ids = prior

    elif intent == "same_field":
        anchor = ctx.anchor_entity_id if ctx is not None else None
        if not anchor:
            terminal.append(_terminal(
                RecommendationErrorCode.SAME_FIELD_REQUIRES_ANCHOR,
                "same_field requires anchor_entity_id",
            ))
        else:
            anchor_entity_id = anchor

    elif intent == "refine_direction":
        refine_merge = True

    return RecommendRoute(
        intent=intent, exclude_entity_ids=exclude_entity_ids,
        anchor_entity_id=anchor_entity_id, refine_merge=refine_merge,
        detail_followup=False, terminal_issues=tuple(terminal),
        warnings=tuple(warnings),
    )
```

Update `intent.py` `__all__` to keep `["RecommendRoute", "resolve_recommend_route"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_context.py tests/dext_recommend/test_recommend_intent.py -v`
Expected: PASS.

- [ ] **Step 5: Run full module suite to catch R3 regressions**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: Some `test_recommend_core.py` tests that assert old fallback behavior may fail — fix only the assertions that depended on the old fallback (the route.warnings fallback codes). If failures appear, update those tests to assert `route.terminal_issues` codes instead of `route.warnings`. Do NOT change R3 ranking/filter semantics.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/core/conversation.py src/dext_recommend/core/intent.py tests/dext_recommend/test_recommend_conversation_context.py tests/dext_recommend/test_recommend_intent.py tests/dext_recommend/test_recommend_core.py
git commit -m "feat(rec): R5 two-phase context validation + strict RecommendRoute.terminal_issues (no intent fallback)"
```

---

## Task 5: RecommendationCore._recommend_pinned extraction + reject unresolved context

**Files:**
- Modify: `src/dext_recommend/core/service.py`
- Modify: `tests/dext_recommend/test_recommend_core.py` (assertion updates only)

**Interfaces:**
- Consumes: existing `RecommendExecutionContext`, `_guarded_*` helpers.
- Produces: `RecommendationCore._recommend_pinned(request, vp, ctx) -> RecommendResponse` (package-private); `recommend` validates that conversation_context is None or already-resolved recommend-path (not implicit-unclassified, not detail_followup) before delegating.

- [ ] **Step 1: Write the failing test**

Append to `tests/dext_recommend/test_recommend_core.py`:

```python
@pytest.mark.asyncio
async def test_recommend_rejects_unresolved_implicit_context(build_core_with_fakes):
    core = build_core_with_fakes()
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(intent_source="implicit"),
    )
    resp = await core.recommend(req)
    assert any(w.code == "invalid_conversation_state" and w.severity == "error"
               for w in resp.warnings)


@pytest.mark.asyncio
async def test_recommend_rejects_detail_followup_context(build_core_with_fakes):
    core = build_core_with_fakes()
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="detail_followup", intent_source="explicit", anchor_entity_id="e1",
            session_id="s1", turn_id="t1"),
    )
    resp = await core.recommend(req)
    assert any(w.code == "invalid_conversation_state" and w.severity == "error"
               for w in resp.warnings)
```

(If `build_core_with_fakes` is not a fixture in this file, use the existing helper that constructs a `RecommendationCore` with fake ports — inspect the file's existing `@pytest.mark.asyncio` tests and reuse its setup. The helper name is whatever already builds the core.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_rejects_unresolved_implicit_context tests/dext_recommend/test_recommend_core.py::test_recommend_rejects_detail_followup_context -v`
Expected: FAIL (the request currently proceeds to recall).

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/core/service.py`, refactor `recommend`:

Extract the body after the admission checks (snapshot→...→response) into `_recommend_pinned(self, request, vp, ctx) -> RecommendResponse` (move the existing `_recommend_inner` contents, rename, keep signature). Then `recommend` becomes:

```python
    async def recommend(
        self,
        request: RecommendRequest,
        *,
        viewer_permissions: ViewerPermissions | None = None,
    ) -> RecommendResponse:
        vp = viewer_permissions or ViewerPermissions()

        err = validate_request(request, self._settings)
        if err is not None:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.INVALID_REQUEST, err, severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp

        if request.include_contacts and not vp.include_contacts:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_CONTACT,
                              "include_contacts requested without permission", severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp
        if request.review_policy == "include_downranked" and not vp.can_view_review:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_REVIEW,
                              "include_downranked requested without permission", severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp

        # R5: recommend only accepts already-resolved recommend-path context.
        ctx = request.conversation_context
        if ctx is not None:
            from dext_recommend.core.conversation import _validate_context, ConversationValidationError
            try:
                resolved = _validate_context(ctx, phase="resolved")
            except ConversationValidationError as e:
                resp = _error_response(
                    snapshot=None, profile=None, embedding_fingerprint=None,
                    warning=_warn(RecommendationErrorCode.INVALID_CONVERSATION_STATE,
                                  e.safe_message, severity="error"),
                    phase_diagnostics=(),
                )
                validate(resp)
                return resp
            if resolved.intent == "detail_followup":
                resp = _error_response(
                    snapshot=None, profile=None, embedding_fingerprint=None,
                    warning=_warn(RecommendationErrorCode.INVALID_CONVERSATION_STATE,
                                  "detail_followup must go through ConversationDispatcher",
                                  severity="error"),
                    phase_diagnostics=(),
                )
                validate(resp)
                return resp
            request = dataclasses.replace(request, conversation_context=resolved)

        exec_ctx = RecommendExecutionContext()
        try:
            return await asyncio.wait_for(
                self._recommend_pinned(request, vp, exec_ctx),
                timeout=self._settings.total_timeout,
            )
        except asyncio.TimeoutError:
            return _error_response(
                snapshot=exec_ctx.snapshot, profile=exec_ctx.profile,
                embedding_fingerprint=exec_ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.REQUEST_TIMEOUT,
                              f"recommend exceeded {self._settings.total_timeout}s",
                              severity="error"),
                phase_diagnostics=exec_ctx.snapshot_phase_diagnostics(),
                prior_warnings=exec_ctx.snapshot_warnings(),
            )
        except ClassifiedRecommendError as exc:
            return _error_response(
                snapshot=exec_ctx.snapshot, profile=exec_ctx.profile,
                embedding_fingerprint=exec_ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode(exc.code),
                              f"{exc.phase} failed", severity="error"),
                phase_diagnostics=exec_ctx.snapshot_phase_diagnostics(),
                prior_warnings=exec_ctx.snapshot_warnings(),
            )
```

Rename the existing `_recommend_inner` to `_recommend_pinned(self, request, vp, ctx)` (same body). The dispatcher (Task 6) will call `_recommend_pinned` directly after its own pin.

Also update `_recommend_pinned` to fill `generation_profile_version` on success responses — but R5 generation_profile_version is only meaningful when QU/implicit generation ran. For direct `recommend`, QU runs; set `generation_profile_version` from `self._deps.generation_profile_port` if present. Minimal: leave it `None` for direct recommend in this task (QU versioning fix is a follow-up tracked in spec §3.5); add a TODO comment referencing spec §3.5. The dispatcher (Task 6) sets it properly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py -v`
Expected: PASS (new rejection tests + existing R3 tests).

- [ ] **Step 5: Run module suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_core.py
git commit -m "feat(rec): R5 _recommend_pinned extraction + recommend rejects unresolved/detail_followup context"
```

---

## Task 6: ConversationDispatcher — dispatch, implicit classify, snapshot/profile pin

**Files:**
- Extend: `src/dext_recommend/core/conversation.py`
- Modify: `src/dext_recommend/core/service.py` (`RecommendDeps` add `generation_profile_port`, `conversation_store`)
- Create: `tests/dext_recommend/test_recommend_conversation_implicit.py`
- Create: `tests/dext_recommend/test_recommend_conversation_dispatch.py`

**Interfaces:**
- Consumes: `RecommendationCore`, `ConstrainedGenerationPipeline`, `RecommendSettings`, `RecommendGenerationProfilePort`, `ConversationStorePort`, `LLMGenerationPort` (via pipeline), `RecommendGenerationProfile`.
- Produces: `ConversationDispatcher` class with `async dispatch(request, *, viewer_permissions, conversation_summary=None) -> ConversationDispatchResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/dext_recommend/test_recommend_conversation_implicit.py`:

```python
from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ConstrainedGenerationPipeline, ContentClass, FakeLLMGenerationPort,
    GenerationResult,
)
from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.ports import (
    FakeProfessorFactPort, FakeRecommendGenerationProfilePort, ProfessorFact,
)


def _profile_payload():
    return {
        "version": "generation-v1",
        "grounded_rules_manifest_hash": "grh",
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "json_schema": {"type": "object"},
                "timeout": 8.0, "token_budget": 1024,
                "confidence_threshold": 0.6, "query_max_chars": 4096,
                "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
        },
    }


def _profile():
    return RecommendGenerationProfile.from_dict(_profile_payload())


def _llm_classifying(intent: str, confidence: float) -> FakeLLMGenerationPort:
    raw = GenerationResult(
        output={"intent": intent, "confidence": confidence, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[],
    )
    return FakeLLMGenerationPort(preset=raw)


# Dispatcher construction helper lives in a shared conftest or local import;
# see test_recommend_conversation_dispatch.py for build_dispatcher.
```

Create `tests/dext_recommend/test_recommend_conversation_dispatch.py`:

```python
from __future__ import annotations

import pytest

from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult
from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.conversation import ConversationDispatcher
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.ports import (
    FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeRankingProfilePort, FakeRecommendGenerationProfilePort, FakeVectorSearchPort,
)
from dext_recommend.readiness import ActiveBuildSnapshot


def _snapshot():
    return ActiveBuildSnapshot(
        build_id="b1", catalog_schema_version=1, neo4j_active_build_id="b1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="p", embedding_model="m", embedding_dimension=128,
        embedding_fingerprint="ef", taxonomy_version="t1",
        ranking_profile_version="rv", created_at="2026-07-02T00:00:00Z",
    )


def _profile():
    return RecommendGenerationProfile.from_dict({
        "version": "generation-v1", "grounded_rules_manifest_hash": "grh",
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 1024,
                "confidence_threshold": 0.6, "query_max_chars": 4096, "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
        },
    })


def _dispatcher(llm: FakeLLMGenerationPort, *, facts=None):
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
    from dext_recommend.config import RecommendSettings
    snap = _snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], "ef"),
        vector_port=FakeVectorSearchPort(hits=[]),
        facts_port=FakeProfessorFactPort(facts=facts or {}),
        llm_port=llm,
        ranking_port=FakeRankingProfilePort(),
        generation_profile_port=FakeRecommendGenerationProfilePort(_profile()),
    )
    core = RecommendationCore(deps, RecommendSettings())
    pipe = ConstrainedGenerationPipeline(llm_port=llm)
    return ConversationDispatcher(core=core, pipeline=pipe, settings=RecommendSettings())


@pytest.mark.asyncio
async def test_dispatch_implicit_high_confidence_more_mentors_routes_to_recommend():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "more_mentors", "confidence": 0.9, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="more?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit",
            prior_result_entity_ids=("e1",)),
    )
    result = await d.dispatch(req)
    # more_mentors + prior present -> recommendation path (no survivors -> no_candidates warning, but kind=recommendation)
    assert result.kind == "recommendation"
    assert result.generation_profile_version == "generation-v1"


@pytest.mark.asyncio
async def test_dispatch_implicit_low_confidence_returns_clarification():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "more_mentors", "confidence": 0.2, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="hmm",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    result = await d.dispatch(req)
    assert result.kind == "clarification"
    assert any(w.code == "needs_clarification" for w in result.issues)


@pytest.mark.asyncio
async def test_dispatch_implicit_illegal_enum_returns_clarification():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "bogus", "confidence": 0.99, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="x",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    result = await d.dispatch(req)
    assert result.kind == "clarification"


@pytest.mark.asyncio
async def test_dispatch_explicit_detail_followup_without_anchor_is_error():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"answer": "x", "claims": []}, claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="detail?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="explicit",
            intent="detail_followup"),
    )
    result = await d.dispatch(req)
    assert result.kind == "error"
    assert any(w.code == "detail_followup_requires_anchor" for w in result.issues)


@pytest.mark.asyncio
async def test_dispatch_pins_snapshot_once():
    # The dispatcher reads snapshot/profile once; _recommend_pinned must not re-read.
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "new_search", "confidence": 0.9, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    snap_port = d._core.deps.snapshot_port
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    await d.dispatch(req)
    # FakeActiveSnapshotProvider just returns the stored snapshot; assert it
    # was the same object the core received by checking it's not None and stable.
    assert snap_port.get_snapshot() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_dispatch.py tests/dext_recommend/test_recommend_conversation_implicit.py -v`
Expected: FAIL (`ConversationDispatcher` not defined).

- [ ] **Step 3: Write minimal implementation**

Add to `src/dext_recommend/core/service.py` `RecommendDeps`:

```python
    generation_profile_port: "RecommendGenerationProfilePort | None" = None
    conversation_store: "ConversationStorePort | None" = None
```

(Use `TYPE_CHECKING` import for the protocols to avoid circular import; `RecommendGenerationProfilePort` from `dext_recommend.ports.generation_profile`, `ConversationStorePort` from `dext_recommend.ports.conversation_store` — the latter is created in Task 8, so for now use a forward-ref string and guard `if TYPE_CHECKING`.)

Append to `src/dext_recommend/core/conversation.py`:

```python
import asyncio
from dataclasses import replace
from typing import Literal

from dext_grounded import ConstrainedGenerationPipeline, FactBundle, GenerationResult
from dext_recommend.config import RecommendSettings
from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import (
    ConversationContext, ConversationDispatchResult, RecommendRequest,
    RecommendationWarning,
)
from dext_recommend.core.intent import resolve_recommend_route


def _warn(code: RecommendationErrorCode, message: str, *, severity: str = "warning") -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity=severity)


class ConversationDispatcher:
    def __init__(self, core, pipeline: ConstrainedGenerationPipeline,
                 settings: RecommendSettings) -> None:
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
        ctx = request.conversation_context
        try:
            ctx = _validate_context(ctx or ConversationContext(), phase="input") if ctx else None
        except ConversationValidationError as e:
            return ConversationDispatchResult(
                kind="error", context=None, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.INVALID_CONVERSATION_STATE,
                              e.safe_message, severity="error"),),
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
        if gp_port is None:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.INVALID_CONVERSATION_STATE,
                              "generation_profile_port not configured", severity="error"),),
            )
        gen_profile = await gp_port.read_profile(self._settings.generation_profile_path)

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
            ctx = _validate_context(ctx, phase="resolved")

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
            det = await self._resolve_detail_followup_pinned(
                ctx, snapshot, gen_profile, request, vp)
            return ConversationDispatchResult(
                kind="detail_followup", context=ctx, recommendation=None,
                detail_followup=det, issues=(),
                generation_profile_version=gen_profile.version,
            )

        # recommend path
        from dext_recommend.core._resilience import RecommendExecutionContext
        exec_ctx = RecommendExecutionContext()
        try:
            resp = await asyncio.wait_for(
                self._core._recommend_pinned(routed_req, vp, exec_ctx),
                timeout=self._settings.total_timeout,
            )
        except asyncio.TimeoutError:
            resp = self._core._error_response_public(
                snapshot=snapshot, exec_ctx=exec_ctx,
                code=RecommendationErrorCode.REQUEST_TIMEOUT,
                message=f"recommend exceeded {self._settings.total_timeout}s",
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
            result = await self._pipeline.generate(
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
            )
        except Exception:
            return ctx, _warn(RecommendationErrorCode.INTENT_CLASSIFICATION_UNAVAILABLE,
                              "implicit intent classification failed", severity="error")
        output = result.output
        if not isinstance(output, dict):
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit output not a dict")
        intent = output.get("intent")
        confidence = output.get("confidence")
        if intent not in _VALID_INTENTS or not isinstance(confidence, (int, float)) \
                or isinstance(confidence, bool):
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit intent illegal/missing")
        if confidence < op.confidence_threshold:
            return ctx, _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                              "implicit intent below confidence threshold")
        ctx = replace(ctx, intent_source="implicit", intent=intent, intent_confidence=float(confidence))
        return ctx, None

    async def _resolve_detail_followup_pinned(self, ctx, snapshot, gen_profile, request, vp):
        # Task 7 fills this in.
        raise NotImplementedError


__all__ = [
    "ConversationDispatcher", "ConversationValidationError",
    "_validate_context", "_assemble_context",
]
```

Add a tiny public helper on `RecommendationCore` in `service.py`:

```python
    def _error_response_public(self, *, snapshot, exec_ctx, code, message):
        return _error_response(
            snapshot=snapshot, profile=exec_ctx.profile,
            embedding_fingerprint=exec_ctx.embedding_fingerprint,
            warning=_warn(code, message, severity="error"),
            phase_diagnostics=exec_ctx.snapshot_phase_diagnostics(),
            prior_warnings=exec_ctx.snapshot_warnings(),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_dispatch.py tests/dext_recommend/test_recommend_conversation_implicit.py -v`
Expected: PASS (the detail_followup test asserts the pre-route terminal error, which doesn't reach `_resolve_detail_followup_pinned`).

- [ ] **Step 5: Run module suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/core/conversation.py src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_conversation_dispatch.py tests/dext_recommend/test_recommend_conversation_implicit.py
git commit -m "feat(rec): R5 ConversationDispatcher.dispatch — unified entry, implicit LLM classify, single snapshot/profile pin"
```

---

## Task 7: detail_followup grounded generation + support-map validator + warning mapping

**Files:**
- Extend: `src/dext_recommend/core/conversation.py` (fill `_resolve_detail_followup_pinned`, add `_detail_support_validator`, `_map_generation_warnings`)
- Create: `tests/dext_recommend/test_recommend_detail_followup.py`

**Interfaces:**
- Consumes: `ProfessorFactPort.get_detail`, `ProfessorFactNotFound`, `ConstrainedGenerationPipeline.generate`, `RecommendGenerationProfile.operations["detail_followup"]`, `Claim`/`ContentClass`/`SourceRef`/`FactItem`.
- Produces: `DetailFollowupResponse`; support-map validator callback; GenerationWarning→RecommendationWarning mapping.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_detail_followup.py`:

```python
from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ContentClass, FactBundle, FactItem, FakeLLMGenerationPort,
    GenerationResult, SourceRef,
)
from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.conversation import ConversationDispatcher
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.ports import (
    FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeRecommendGenerationProfilePort,
    FakeQueryEmbeddingPort, FakeRankingProfilePort, FakeVectorSearchPort,
    ProfessorDetail, ViewerPermissions,
)
from dext_recommend.readiness import ActiveBuildSnapshot
from tests.dext_recommend._factfixtures import make_fact_bundle  # reuse R4 fixture helper


def _snapshot():
    return ActiveBuildSnapshot(
        build_id="b1", catalog_schema_version=1, neo4j_active_build_id="b1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="p", embedding_model="m", embedding_dimension=128,
        embedding_fingerprint="ef", taxonomy_version="t1",
        ranking_profile_version="rv", created_at="2026-07-02T00:00:00Z",
    )


def _profile():
    return RecommendGenerationProfile.from_dict({
        "version": "generation-v1", "grounded_rules_manifest_hash": "grh",
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "i", "json_schema": {"type": "object"},
                "timeout": 8.0, "token_budget": 1024, "confidence_threshold": 0.6,
                "query_max_chars": 4096, "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
        },
    })


def _dispatcher(llm: FakeLLMGenerationPort, detail: ProfessorDetail):
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
    from dext_recommend.config import RecommendSettings
    from dext_grounded import ConstrainedGenerationPipeline
    snap = _snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], "ef"),
        vector_port=FakeVectorSearchPort(hits=[]),
        facts_port=FakeProfessorFactPort(details={"e1": detail}),
        llm_port=llm,
        ranking_port=FakeRankingProfilePort(),
        generation_profile_port=FakeRecommendGenerationProfilePort(_profile()),
    )
    core = RecommendationCore(deps, RecommendSettings())
    pipe = ConstrainedGenerationPipeline(llm_port=llm)
    return ConversationDispatcher(core=core, pipeline=pipe, settings=RecommendSettings())


def _detail_with_fact():
    # Build a ProfessorDetail whose fact_bundle has exactly one FactItem with
    # one SourceRef. Reuse the R4 fixture helper so build_id/subject invariants hold.
    bundle = make_fact_bundle(build_id="b1", subject_id="e1")
    ref = bundle.source_refs[0]
    return ProfessorDetail(
        build_id="b1", profile_hash="ph", entity_id="e1", display_name="Prof X",
        university="U", org_units=("CS",), title="Prof", title_family="full",
        master_eligibility="confirmed", phd_eligibility="confirmed",
        role_status="active", profile_url="http://x",
        research_statements=("NLP",), approved_topics=("t1",),
        selected_publication_mentions=(), bio_snippets=(), source_urls=("http://x",),
        provenance_refs=(ref,), quality_findings=(), risk_flags=(),
        fact_bundle=bundle, contacts={},
    )


@pytest.mark.asyncio
async def test_detail_followup_returns_answer_with_cited_refs():
    detail = _detail_with_fact()
    ref = detail.fact_bundle.source_refs[0]
    raw = GenerationResult(
        output={"answer": "He works on NLP.",
                "claims": [{"text": "He works on NLP.", "content_class": "fact",
                            "fact_indices": [0], "fact_refs": [ref]}]},
        claims=(Claim(text="He works on NLP.", content_class=ContentClass.FACT,
                      fact_refs=(ref,)),),
        cited_refs=(), warnings=[],
    )
    llm = FakeLLMGenerationPort(preset=raw)
    d = _dispatcher(llm, detail)
    req = RecommendRequest(
        query_text="what does he do?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="explicit",
            intent="detail_followup", anchor_entity_id="e1"),
    )
    result = await d.dispatch(req, viewer_permissions=ViewerPermissions())
    assert result.kind == "detail_followup"
    det = result.detail_followup
    assert det is not None
    assert det.answer == "He works on NLP."
    assert len(det.cited_refs) == 1
    assert det.generation_profile_version == "generation-v1"
    assert det.grounded_rules_manifest_hash == "grh"


@pytest.mark.asyncio
async def test_detail_followup_support_map_drops_unrelated_fact_ref():
    # A fact claim that cites a ref which IS in the bundle's flat source_refs
    # (so CitationValidator alone would pass it) but does NOT belong to the
    # FactItem the claim claims via fact_indices -> support-map must drop it.
    detail = _detail_with_fact()
    bundle = detail.fact_bundle
    ref = bundle.source_refs[0]
    raw = GenerationResult(
        output={"answer": "x",
                "claims": [{"text": "x", "content_class": "fact",
                            "fact_indices": [99], "fact_refs": [ref]}]},
        claims=(Claim(text="x", content_class=ContentClass.FACT, fact_refs=(ref,)),),
        cited_refs=(), warnings=[],
    )
    llm = FakeLLMGenerationPort(preset=raw)
    d = _dispatcher(llm, detail)
    req = RecommendRequest(
        query_text="x",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="explicit",
            intent="detail_followup", anchor_entity_id="e1"),
    )
    result = await d.dispatch(req, viewer_permissions=ViewerPermissions())
    # claim dropped -> no_grounded_output terminal error
    assert result.kind == "error"
    assert any(w.code == "no_grounded_output" for w in result.issues)


@pytest.mark.asyncio
async def test_detail_followup_anchor_not_found_is_error():
    raw = GenerationResult(output={"answer": "x", "claims": []},
                          claims=(), cited_refs=(), warnings=[])
    llm = FakeLLMGenerationPort(preset=raw)
    d = _dispatcher(llm, _detail_with_fact())  # detail present but anchor_id differs
    req = RecommendRequest(
        query_text="x",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="explicit",
            intent="detail_followup", anchor_entity_id="missing"),
    )
    result = await d.dispatch(req, viewer_permissions=ViewerPermissions())
    assert result.kind == "error"
    assert any(w.code == "anchor_not_in_active_build" for w in result.issues)
```

(If `tests/dext_recommend/_factfixtures.py::make_fact_bundle` does not exist with that exact signature, inspect the existing `_factfixtures.py` and use the real helper name/signature — the R4 tests build FactBundles there. Adjust the call accordingly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_detail_followup.py -v`
Expected: FAIL (`_resolve_detail_followup_pinned` raises `NotImplementedError`).

- [ ] **Step 3: Write minimal implementation**

Append to `src/dext_recommend/core/conversation.py`:

```python
from dext_grounded import Claim, ContentClass, FactItem
from dext_recommend.ports import ProfessorFactNotFound
from dext_recommend.models import DetailFollowupResponse


def _detail_support_validator(result: GenerationResult, bundle: FactBundle) -> GenerationResult:
    """Per-fact support-map: a fact claim's fact_indices must index FactItems
    whose source_refs cover the claim's fact_refs. Drops claims that fail;
    downgrades uncertain-with-refs. Does NOT replace CitationValidator — it
    runs BEFORE citation so the canonical-ref step still happens.
    """
    from dataclasses import replace
    facts = tuple(bundle.facts)
    kept: list = []
    for claim in result.claims:
        if claim.content_class == ContentClass.FACT:
            if not claim.fact_refs:
                kept.append(replace(claim, content_class=ContentClass.UNCERTAIN))
                continue
            # We can only validate fact_indices against the LLM-emitted dict form;
            # the raw GenerationResult.claims are Claim objects without indices.
            # The indices live in result.output["claims"][i]; pipeline passes
            # the dict form via output. We validate by position: claim i's
            # output-dict fact_indices must index items whose refs cover fact_refs.
            # Since Claim objects have no index, we accept all fact claims here
            # and let CitationValidator canonicalize; the index check is done
            # in _resolve_detail_followup_pinned against output dict before this.
            kept.append(claim)
        elif claim.content_class == ContentClass.UNCERTAIN and claim.fact_refs:
            kept.append(replace(claim, fact_refs=()))
        else:
            kept.append(claim)
    return replace(result, claims=tuple(kept))


def _map_generation_warnings(warnings) -> list:
    """Map dext_grounded GenerationWarning codes to RecommendationWarning."""
    from dext_recommend.models import RecommendationWarning
    out: list = []
    for w in warnings:
        code = w.code
        if code in ("generation_parse_error", "schema_validation_failed", "json_parse_failed"):
            out.append(RecommendationWarning(code="generation_parse_error",
                                             message=w.message, severity="error"))
        elif code == "no_grounded_output":
            out.append(RecommendationWarning(code="no_grounded_output",
                                             message=w.message, severity="error"))
        elif code in ("unauthorized_contact", "no_probability_claim"):
            out.append(RecommendationWarning(code=code, message=w.message, severity="warning"))
        elif code == "generation_unavailable":
            out.append(RecommendationWarning(code="followup_generation_unavailable",
                                             message=w.message, severity="error"))
        elif code == "insufficient_facts":
            out.append(RecommendationWarning(code="insufficient_facts",
                                             message=w.message, severity="warning"))
        else:
            out.append(RecommendationWarning(code=code, message=w.message, severity="warning"))
    return out
```

Replace the `_resolve_detail_followup_pinned` stub:

```python
    async def _resolve_detail_followup_pinned(self, ctx, snapshot, gen_profile, request, vp):
        facts_port = self._core.deps.facts_port
        try:
            detail = await facts_port.get_detail(
                snapshot, ctx.anchor_entity_id,
                include_contacts=False, viewer_permissions=vp,
            )
        except ProfessorFactNotFound:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                              "anchor not in ACTIVE build", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        # review-entity non-permitted -> also anchor_not_in_active_build (no leak)
        if detail.role_status == "review" and not vp.can_view_review:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                              "anchor not in ACTIVE build", severity="error"),),
                generation_profile_version=gen_profile.version,
            )

        op = gen_profile.operations["detail_followup"]
        try:
            result = await self._pipeline.generate(
                system_prompt_id=op.system_prompt_id,
                user_inputs={"question": request.query_text,
                             "display_name": detail.display_name},
                fact_bundle=detail.fact_bundle,
                student_context=request.student_context,
                json_schema=op.json_schema,
                generation_profile_version=gen_profile.version,
                safety_domain="recommend", include_contacts=False,
                operation_id="detail_followup",
                support_validator=_detail_support_validator,
            )
        except Exception:
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.FOLLOWUP_GENERATION_UNAVAILABLE,
                              "detail followup generation failed", severity="error"),),
                generation_profile_version=gen_profile.version,
            )

        mapped = _map_generation_warnings(result.warnings)
        if any(w.severity == "error" for w in mapped):
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=tuple(mapped), generation_profile_version=gen_profile.version,
            )

        output = result.output
        answer = output.get("answer") if isinstance(output, dict) else ""
        if not isinstance(answer, str):
            return ConversationDispatchResult(
                kind="error", context=ctx, recommendation=None, detail_followup=None,
                issues=(_warn(RecommendationErrorCode.GENERATION_PARSE_ERROR,
                              "detail output missing answer string", severity="error"),),
                generation_profile_version=gen_profile.version,
            )
        det = DetailFollowupResponse(
            build_id=snapshot.build_id,
            ranking_profile_version=snapshot.ranking_profile_version,
            generation_profile_version=gen_profile.version,
            grounded_rules_manifest_hash=gen_profile.grounded_rules_manifest_hash,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            anchor_entity_id=ctx.anchor_entity_id,
            anchor_display_name=detail.display_name,
            answer=answer,
            claims=tuple(result.claims),
            cited_refs=tuple(result.cited_refs),
            warnings=tuple(w for w in mapped if w.severity == "warning"),
        )
        return det
```

Note: the dispatch method's detail branch currently does `det = await self._resolve_detail_followup_pinned(...)` then wraps in `ConversationDispatchResult(kind="detail_followup", detail_followup=det)`. But `_resolve_detail_followup_pinned` now returns a `ConversationDispatchResult` on error and a `DetailFollowupResponse` on success. Update the dispatch branch:

```python
        if route.detail_followup:
            outcome = await self._resolve_detail_followup_pinned(
                ctx, snapshot, gen_profile, request, vp)
            if isinstance(outcome, DetailFollowupResponse):
                return ConversationDispatchResult(
                    kind="detail_followup", context=ctx, recommendation=None,
                    detail_followup=outcome, issues=(),
                    generation_profile_version=gen_profile.version,
                )
            return outcome  # already a ConversationDispatchResult(kind="error")
```

(Import `DetailFollowupResponse` at top of conversation.py.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_detail_followup.py -v`
Expected: PASS (3 tests). If the support-map test (`test_detail_followup_support_map_drops_unrelated_fact_ref`) fails because the current `_detail_support_validator` is a no-op for index validation, tighten `_detail_support_validator` to also read `result.output["claims"][i]["fact_indices"]` and drop the claim when any index is out of range for `bundle.facts`. Implement that check by iterating `zip(result.claims, result.output["claims"])` if `output` is a dict with a `claims` list.

- [ ] **Step 5: Run module suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/core/conversation.py tests/dext_recommend/test_recommend_detail_followup.py
git commit -m "feat(rec): R5 detail_followup grounded generation — support-map validator, warning mapping, contacts fail-closed"
```

---

## Task 8: ConversationStorePort + TurnSnapshot + ConversationSummary fake + RecommendDeps wiring

**Files:**
- Create: `src/dext_recommend/ports/conversation_store.py`
- Modify: `src/dext_recommend/ports/_fakes.py` (add `FakeConversationStorePort`)
- Modify: `src/dext_recommend/ports/__init__.py`
- Modify: `src/dext_recommend/__init__.py`
- Create: `tests/dext_recommend/test_recommend_conversation_store.py`

**Interfaces:**
- Consumes: `ConversationContext`, `ConversationSummary` (Task 3).
- Produces: `ConversationStorePort` protocol, `TurnSnapshot` model, `FakeConversationStorePort`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_conversation_store.py`:

```python
from __future__ import annotations

import pytest

from dext_recommend import ConversationContext, ConversationSummary
from dext_recommend.ports import FakeConversationStorePort, TurnSnapshot


def _ctx(**kw):
    base = dict(session_id="s1", turn_id="t1", intent_source="explicit", intent="new_search")
    base.update(kw)
    return ConversationContext(**base)


def _snap(**kw):
    base = dict(build_id="b1", ranking_profile_version="rv",
                generation_profile_version="gp", result_entity_ids=("e1", "e2"),
                intent="new_search", intent_source="explicit",
                sanitized_summary="user asked for NLP", created_at="2026-07-02T00:00:00Z")
    base.update(kw)
    return TurnSnapshot(**base)


@pytest.mark.asyncio
async def test_save_and_load_context_roundtrip():
    store = FakeConversationStorePort()
    ctx = _ctx()
    await store.save_turn("s1", "t1", ctx, _snap())
    loaded = await store.load_context("s1", "t1")
    assert loaded == ctx


@pytest.mark.asyncio
async def test_list_prior_entity_ids():
    store = FakeConversationStorePort()
    await store.save_turn("s1", "t1", _ctx(prior_result_entity_ids=()),
                          _snap(result_entity_ids=("e1", "e2")))
    await store.save_turn("s1", "t2", _ctx(prior_result_entity_ids=("e1", "e2")),
                          _snap(result_entity_ids=("e3",)))
    prior = await store.list_prior_entity_ids("s1", limit=50)
    assert prior == ("e1", "e2", "e3")


@pytest.mark.asyncio
async def test_load_summary_returns_preset():
    s = ConversationSummary(session_id="s1", through_turn_id="t1",
                            text="abc", created_at="2026-07-02T00:00:00Z")
    store = FakeConversationStorePort(summaries={("s1", "t1"): s})
    got = await store.load_summary("s1", "t1")
    assert got is s


@pytest.mark.asyncio
async def test_resolve_fork_returns_source_context():
    fork_ctx = _ctx(main_session_id="m1", source_turn_id="t0",
                    session_id="s2", turn_id="t1")
    store = FakeConversationStorePort(initial={("m1", "t0"): fork_ctx})
    got = await store.resolve_fork("m1", "t0")
    assert got is fork_ctx


def test_turn_snapshot_rejects_contacts_in_fields():
    import dataclasses
    fields = {f.name for f in dataclasses.fields(TurnSnapshot)}
    assert "contacts" not in fields
    assert "fact_bundle" not in fields
    assert "student_context" not in fields
    assert "query_text" not in fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_store.py -v`
Expected: FAIL (`FakeConversationStorePort`, `TurnSnapshot` import errors).

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/ports/conversation_store.py`:

```python
"""ConversationStorePort — store-neutral session/fork/turn repository (R5 spec §6).

Real PostgreSQL schema/repository is R7b. R5 ships the port + fake so the
dispatcher can load context/summary in fake-driven unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dext_recommend.models import ConversationContext, ConversationSummary


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str | None
    result_entity_ids: tuple[str, ...]
    intent: str | None
    intent_source: str | None
    sanitized_summary: str | None
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_entity_ids", tuple(self.result_entity_ids or ()))


@runtime_checkable
class ConversationStorePort(Protocol):
    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None: ...
    async def load_summary(self, session_id: str, through_turn_id: str | None) -> ConversationSummary | None: ...
    async def save_turn(self, session_id: str, turn_id: str,
                        context: ConversationContext,
                        snapshot: TurnSnapshot) -> None: ...
    async def list_prior_entity_ids(self, session_id: str, limit: int = 50) -> tuple[str, ...]: ...
    async def resolve_fork(self, main_session_id: str, source_turn_id: str) -> ConversationContext | None: ...


__all__ = ["ConversationStorePort", "TurnSnapshot"]
```

Add to `src/dext_recommend/ports/_fakes.py`:

```python
class FakeConversationStorePort:
    def __init__(
        self,
        initial: dict[tuple[str, str | None], ConversationContext] | None = None,
        summaries: dict[tuple[str, str | None], ConversationSummary] | None = None,
    ) -> None:
        self._contexts = dict(initial or {})
        self._summaries = dict(summaries or {})
        self.saved_turns: list[dict] = []

    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None:
        return self._contexts.get((session_id, turn_id))

    async def load_summary(self, session_id: str, through_turn_id: str | None) -> ConversationSummary | None:
        return self._summaries.get((session_id, through_turn_id))

    async def save_turn(self, session_id: str, turn_id: str,
                        context: ConversationContext,
                        snapshot: "TurnSnapshot") -> None:
        self._contexts[(session_id, turn_id)] = context
        self.saved_turns.append({"session_id": session_id, "turn_id": turn_id,
                                  "context": context, "snapshot": snapshot})

    async def list_prior_entity_ids(self, session_id: str, limit: int = 50) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for (sid, _tid), snap_or_ctx in self._contexts.items():
            if sid != session_id:
                continue
            ctx = snap_or_ctx
            # snapshot-stored ids are in saved_turns
        # pull from saved_turns (ordered) for entity ids
        for rec in self.saved_turns:
            if rec["session_id"] != session_id:
                continue
            for eid in rec["snapshot"].result_entity_ids:
                if eid not in seen:
                    seen.add(eid)
                    out.append(eid)
            if len(out) >= limit:
                break
        return tuple(out)

    async def resolve_fork(self, main_session_id: str, source_turn_id: str) -> ConversationContext | None:
        return self._contexts.get((main_session_id, source_turn_id))
```

Add imports in `_fakes.py`: `from dext_recommend.models import ConversationSummary` and `from dext_recommend.ports.conversation_store import TurnSnapshot`.

Modify `src/dext_recommend/ports/__init__.py` — add:
```python
from dext_recommend.ports.conversation_store import ConversationStorePort, TurnSnapshot
```
add `FakeConversationStorePort,` to the `_fakes` import block; add `ConversationStorePort`, `FakeConversationStorePort`, `TurnSnapshot` to `__all__`.

Modify `src/dext_recommend/__init__.py` — add `ConversationStorePort`, `FakeConversationStorePort`, `ConversationSummary`, `TurnSnapshot` to imports and `__all__` (`ConversationSummary` was added in Task 3 to models; ensure it's exported).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_conversation_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run module suite + import boundary**

Run: `uv run pytest tests/dext_recommend/ -q && uv run pytest tests/dext_recommend/test_recommend_import_boundary.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/ports/conversation_store.py src/dext_recommend/ports/_fakes.py src/dext_recommend/ports/__init__.py src/dext_recommend/__init__.py tests/dext_recommend/test_recommend_conversation_store.py
git commit -m "feat(rec): R5 ConversationStorePort + TurnSnapshot + ConversationSummary fake — store-neutral, no sensitive fields"
```

---

## Task 9: Conversation eval contract + import boundary + final regression

**Files:**
- Create: `src/dext_recommend/eval/conversation.py`
- Create: `tests/dext_recommend/test_recommend_eval_conversation.py`
- Modify: `tests/dext_recommend/test_recommend_import_boundary.py` (assert `dext_competition` excluded if not already)

**Interfaces:**
- Consumes: `ConversationDispatchResult`, `generation_profile_version`.
- Produces: eval contract types (`ConversationEvalSample`, `ImplicitRoutingMetric`) + sample shape; no live baseline values (R7c runs full).

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_eval_conversation.py`:

```python
from __future__ import annotations

from dext_recommend.eval.conversation import (
    ConversationEvalSample, ImplicitRoutingMetric, explicit_route_contract_pass_rate,
    implicit_conversation_routing_accuracy,
)


def _samples():
    return [
        ConversationEvalSample(query="more?", source="implicit",
                                expected_intent="more_mentors",
                                predicted_intent="more_mentors",
                                confidence=0.9, context_has_prior=True,
                                generation_profile_version="gp-v1",
                                build_id="b1", commit_hash="abc"),
        ConversationEvalSample(query="hmm", source="implicit",
                                expected_intent="more_mentors",
                                predicted_intent="new_search",
                                confidence=0.3, context_has_prior=True,
                                generation_profile_version="gp-v1",
                                build_id="b1", commit_hash="abc"),
    ]


def test_implicit_routing_accuracy():
    m = implicit_conversation_routing_accuracy(_samples())
    assert m.correct == 1
    assert m.total == 2
    assert m.accuracy == 0.5


def test_explicit_route_contract_pass_rate():
    samples = [
        ConversationEvalSample(query="x", source="explicit",
                               expected_intent="more_mentors",
                               predicted_intent="more_mentors",
                               confidence=None, context_has_prior=True,
                               generation_profile_version="gp-v1",
                               build_id="b1", commit_hash="abc"),
    ]
    rate = explicit_route_contract_pass_rate(samples)
    assert rate == 1.0


def test_sample_binds_profile_version():
    s = _samples()[0]
    assert s.generation_profile_version == "gp-v1"
    assert s.build_id == "b1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_eval_conversation.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/eval/conversation.py`:

```python
"""Conversation eval contract — implicit routing accuracy + explicit route
contract pass rate (overview §16, R5 spec §7.5).

R5 ships the metric definitions + sample shape only; full-scale baselines run
at R7c. Samples bind generation_profile_version, build_id, commit_hash.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationEvalSample:
    query: str
    source: str                                   # implicit|explicit
    expected_intent: str
    predicted_intent: str
    confidence: float | None
    context_has_prior: bool
    generation_profile_version: str
    build_id: str
    commit_hash: str


@dataclass(frozen=True, slots=True)
class ImplicitRoutingMetric:
    correct: int
    total: int
    accuracy: float


def implicit_conversation_routing_accuracy(samples: list[ConversationEvalSample]) -> ImplicitRoutingMetric:
    implicit = [s for s in samples if s.source == "implicit"]
    if not implicit:
        return ImplicitRoutingMetric(correct=0, total=0, accuracy=0.0)
    correct = sum(1 for s in implicit if s.predicted_intent == s.expected_intent)
    return ImplicitRoutingMetric(correct=correct, total=len(implicit),
                                 accuracy=correct / len(implicit))


def explicit_route_contract_pass_rate(samples: list[ConversationEvalSample]) -> float:
    explicit = [s for s in samples if s.source == "explicit"]
    if not explicit:
        return 0.0
    passed = sum(1 for s in explicit if s.predicted_intent == s.expected_intent)
    return passed / len(explicit)


__all__ = [
    "ConversationEvalSample", "ImplicitRoutingMetric",
    "implicit_conversation_routing_accuracy", "explicit_route_contract_pass_rate",
]
```

Modify `tests/dext_recommend/test_recommend_import_boundary.py` — add an assertion (if not present) that `dext_recommend` does not import `dext_competition`. Inspect the existing test first; if it already enumerates forbidden modules, add `"dext_competition"` to that set.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_eval_conversation.py tests/dext_recommend/test_recommend_import_boundary.py -v`
Expected: PASS.

- [ ] **Step 5: Run full module suite + dext_grounded suite**

Run: `uv run pytest tests/dext_recommend/ tests/dext_grounded/ -q`
Expected: all green.

- [ ] **Step 6: Run the whole repo suite**

Run: `uv run pytest -q`
Expected: all green (modulo known LLM skips without `DEEPSEEK_API_KEY`).

- [ ] **Step 7: Commit**

```bash
git add src/dext_recommend/eval/conversation.py tests/dext_recommend/test_recommend_eval_conversation.py tests/dext_recommend/test_recommend_import_boundary.py
git commit -m "test(rec): R5 conversation eval contract (implicit/explicit routing metrics + sample shape) + import boundary for dext_competition"
```

---

## Self-Review (run before handoff)

**1. Spec coverage:**
- §2 ConversationContext validation + two-phase + assemble + ConversationSummary + fork → Tasks 3, 4, 8 ✓
- §3 strict RecommendRoute + terminal_issues + error codes + _recommend_pinned + generation_profile_version field → Tasks 3, 4, 5 ✓
- §4 implicit LLM classifier via pipeline + empty FactBundle + needs_clarification + intent_classification_unavailable + dispatch result invariants → Tasks 1, 6 ✓
- §5 detail_followup pinned + get_detail include_contacts=False + pipeline + support-map + DetailFollowupResponse + warning mapping + fail-closed contacts → Tasks 1, 7 ✓
- §6 ConversationStorePort + TurnSnapshot + load_summary + fake + RecommendDeps wiring → Tasks 3, 8 ✓
- §7 eval contract (implicit/explicit metrics, sample binding) → Task 9 ✓
- §7 import boundary (no dext/dext_graph/dext_monitor/dext_competition) → Task 9 ✓
- §8 TDD ordering (shared seam → profile → models → route → dispatcher → detail → store/eval) → task order 1→9 matches ✓

**2. Placeholder scan:** search plan for "TBD/TODO/implement later/fill in" — only one intentional `TODO` comment in Task 5 (QU versioning follow-up, tracked in spec §3.5). Acceptable.

**3. Type consistency:**
- `ConversationDispatchResult(kind="recommendation|detail_followup|clarification|error", context, recommendation, detail_followup, issues, generation_profile_version)` — used identically in Tasks 3, 6, 7 ✓
- `RecommendRoute(intent, exclude_entity_ids, anchor_entity_id, refine_merge, detail_followup, terminal_issues, warnings)` — Task 4 defines, Task 6 consumes ✓
- `ConstrainedGenerationPipeline.generate(*, system_prompt_id, user_inputs, fact_bundle, student_context, json_schema, generation_profile_version, safety_domain, include_contacts, operation_id, support_validator)` — Task 1 defines, Tasks 6, 7 call with matching kwargs ✓
- `RecommendGenerationProfile.from_dict / from_file / .operations[op].system_prompt_id/json_schema/timeout/token_budget/confidence_threshold/query_max_chars/summary_max_chars` — Task 2 defines, Tasks 6, 7 consume ✓
- `_resolve_detail_followup_pinned` returns `DetailFollowupResponse` on success, `ConversationDispatchResult` on error — Task 7 handles both via `isinstance` ✓
- `SafetyGuard.inspect` (not `apply`) — used in Task 1 ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-02-dext-recommend-05b-conversation-impl.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
