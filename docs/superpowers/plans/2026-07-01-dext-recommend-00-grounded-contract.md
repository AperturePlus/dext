# dext_grounded 共享契约 Implementation Plan (R0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared `dext_grounded` contract package (constrained LLM generation + fact citation + content classification) consumed peer-to-peer by `dext_recommend` and (later) `dext_competition`, with no real LLM calls — only protocols, dataclasses, validators, and guards.

**Architecture:** A 4th peer package `src/dext_grounded/` defines the immutable data contracts (`StudentContext`, `SourceRef`, `UserContextRef`, `ContentClass`, `FactBundle`/`FactItem`, `Claim`, `GenerationResult`) and three pure-Python protocols (`LLMGenerationPort`, `CitationValidator`, `SafetyGuard`) from the grounded-generation spec. No I/O, no LLM client, no DB — these are interfaces and pure rule logic. `dext_recommend` and `dext_competition` import this package but never import each other.

**Tech Stack:** Python 3.11, stdlib `dataclasses`/`enum`/`hashlib`, pydantic v2 (already a dep), pytest (asyncio_mode="auto").

## Global Constraints

Copied verbatim from the grounded-generation spec §2–§8 and CLAUDE.md:
- `gpa_bucket`、`rank_bucket` 必须是分桶枚举，不得接收或记录原值。
- `StudentContext` 不进入 catalog、Neo4j、Qdrant 教师事实图，也不进入竞赛知识库索引。
- 推荐日志不得记录 `StudentContext` 原文；只记录完成度 bucket 和是否使用。
- `FactBundle` 是只读、不可变快照；单次生成请求内不刷新。
- LLM 生成只能消费传入 `FactBundle` 的 `facts`，不得从训练记忆补充教师事实或赛事规则。
- `FactItem` 缺少 `source_refs` 时必须标记 `uncertain`，并计入 groundedness 评测。
- `fact` 类 `Claim` 的 `fact_refs` 必须非空且每条都能在 `FactBundle.source_refs` 内找到。
- API key 只从环境读取，不进入 prompt、日志、exception repr 或 generation profile。
- `generate` 必须在 LLM 返回后立即进入引用校验，不得把未校验输出直接返回调用方。
- 公开 API 默认不暴露 `doc_path`、`heading_path` 或 `chunk_hash`，除非 diagnostics/debug/admin 模式显式开启。
- 平台 Windows + Git Bash；`LF will be replaced by CRLF` git warnings are benign。
- KISS / no premature optimization；prefer many small focused modules over one tangled file。
- 每个 package 的 `__init__.py` re-export 公开接口（`__all__`）；modules form an acyclic import DAG。

---

## File Structure

```text
src/dext_grounded/
  __init__.py              # re-export public surface, __all__
  content.py               # ContentClass enum
  student_context.py       # StudentContext (脱敏分桶用户背景)
  source_ref.py           # SourceRef + UserContextRef
  fact_bundle.py           # FactBundle + FactItem (只读不可变快照)
  claims.py                # Claim + GenerationResult + GenerationWarning
  ports.py                 # LLMGenerationPort (Protocol)
  citation.py              # CitationValidator (纯函数, 逐 Claim 校验)
  safety.py                # SafetyGuard (规则检查, 概率承诺/伪造引用/越权联系方式)
  profile.py               # generation_profile_version 模型 + ProfileRegistry
tests/
  test_grounded_import_boundary.py
  test_grounded_student_context.py
  test_grounded_source_ref.py
  test_grounded_fact_bundle.py
  test_grounded_claims.py
  test_grounded_citation.py
  test_grounded_safety.py
  test_grounded_profile.py
  test_grounded_port_contract.py
```

**Responsibilities:** `content.py` (enum only), `student_context.py`/`source_ref.py`/`fact_bundle.py`/`claims.py` (immutable dataclasses), `ports.py` (the single LLM protocol), `citation.py` (pure claim validation against a FactBundle), `safety.py` (pure rule-based output filtering), `profile.py` (version registry). Acyclic DAG: `__init__` → `claims`/`fact_bundle`/`ports` → `source_ref`/`student_context`/`content`; `citation`/`safety` → `claims`/`fact_bundle`/`source_ref`; nothing imports `__init__`.

---

### Task 1: Create package skeleton + import boundary test

**Files:**
- Create: `src/dext_grounded/__init__.py`
- Create: `tests/test_grounded_import_boundary.py`

**Interfaces:**
- Produces: package `dext_grounded` importable; `__all__` empty for now (filled by later tasks).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_import_boundary.py
"""dext_grounded must be importable and must NOT import dext / dext_graph / dext_monitor."""
from __future__ import annotations

import importlib
import sys


def test_dext_grounded_is_importable():
    mod = importlib.import_module("dext_grounded")
    assert mod is not None
    assert mod.__name__ == "dext_grounded"


def test_dext_grounded_does_not_import_dext_family():
    # import dext_grounded fresh, then check no dext* peer leaked in.
    for name in list(sys.modules):
        if name in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
            del sys.modules[name]
    importlib.import_module("dext_grounded")
    for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
        assert forbidden not in sys.modules, (
            f"dext_grounded must not import peer module {forbidden!r}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_import_boundary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_grounded'`

- [ ] **Step 3: Create the package**

```python
# src/dext_grounded/__init__.py
"""Shared constrained-generation + fact-citation contract.

Peer-to-peer contract layer consumed by ``dext_recommend`` and (later)
``dext_competition``. Defines immutable data contracts and three pure-Python
protocols (LLMGenerationPort, CitationValidator, SafetyGuard) from the
grounded-generation spec. No I/O, no LLM client, no DB.

Both consumer modules import this package but never import each other.
"""

__version__ = "0.1.0"

__all__: list[str] = []
```

Also register the package in `pyproject.toml` wheel targets. Modify:

```toml
# pyproject.toml — [tool.hatch.build.targets.wheel] packages list
packages = ["src/dext", "src/dext_graph", "src/dext_monitor", "src/dext_grounded"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_import_boundary.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/__init__.py tests/test_grounded_import_boundary.py pyproject.toml
git commit -m "feat(grounded): scaffold dext_grounded peer contract package"
```

---

### Task 2: ContentClass enum

**Files:**
- Create: `src/dext_grounded/content.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_content.py`

**Interfaces:**
- Produces: `ContentClass` enum with members `FACT="fact"`, `ADVICE="advice"`, `UNCERTAIN="uncertain"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_content.py
from __future__ import annotations

from dext_grounded import ContentClass


def test_content_class_members():
    assert ContentClass.FACT.value == "fact"
    assert ContentClass.ADVICE.value == "advice"
    assert ContentClass.UNCERTAIN.value == "uncertain"


def test_content_class_is_exhaustive_for_spec():
    # spec §2.3: exactly fact | advice | uncertain
    members = {m.value for m in ContentClass}
    assert members == {"fact", "advice", "uncertain"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_content.py -v`
Expected: FAIL with `ImportError: cannot import name 'ContentClass' from 'dext_grounded'`

- [ ] **Step 3: Implement ContentClass**

```python
# src/dext_grounded/content.py
"""Output content classification (grounded-generation spec §2.3).

Every assertion (Claim) is independently classified; mixed fact/advice/uncertain
output is NOT collapsed into a single global content_class.
"""
from __future__ import annotations

from enum import Enum


class ContentClass(str, Enum):
    FACT = "fact"          # from the fact bundle or an official link
    ADVICE = "advice"      # process/methodology + user-context advice
    UNCERTAIN = "uncertain"  # needs current-year notice or school-file re-check


__all__ = ["ContentClass"]
```

Update `__init__.py` to re-export:

```python
# src/dext_grounded/__init__.py — replace __all__ block
from dext_grounded.content import ContentClass

__all__ = ["ContentClass"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_content.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/content.py src/dext_grounded/__init__.py tests/test_grounded_content.py
git commit -m "feat(grounded): add ContentClass enum"
```

---

### Task 3: StudentContext (脱敏分桶用户背景)

**Files:**
- Create: `src/dext_grounded/student_context.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_student_context.py`

**Interfaces:**
- Produces: `StudentContext` frozen dataclass with fields from spec §2.1; `gpa_bucket`/`rank_bucket` accept only non-empty string buckets (no raw values); `research_interests` defaults to empty list; `safe_log_summary()` returns a redacted dict for logs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_student_context.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import StudentContext


def test_student_context_defaults():
    ctx = StudentContext()
    assert ctx.education_stage is None
    assert ctx.school is None
    assert ctx.research_interests == []
    assert ctx.profile_completeness is None


def test_student_context_is_frozen():
    ctx = StudentContext(school="X University")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.school = "Y University"


def test_student_context_safe_log_summary_omits_raw_values():
    ctx = StudentContext(
        education_stage="本科高年级",
        school="某大学",
        major="CS",
        gpa_bucket="top10",
        rank_bucket="top5",
        research_interests=["NLP"],
        achievements_summary="won a national prize",
        competition_experience_summary="ICPC regional",
        profile_completeness=0.8,
    )
    summary = ctx.safe_log_summary()
    # only completeness bucket + whether used, never raw text
    assert summary["uses_profile"] is True
    assert summary["profile_completeness"] == 0.8
    assert summary["education_stage"] == "本科高年级"
    for forbidden in ("school", "major", "gpa_bucket", "rank_bucket",
                       "research_interests", "achievements_summary",
                       "competition_experience_summary"):
        assert forbidden not in summary, f"safe_log_summary must not expose {forbidden}"


def test_student_context_safe_log_summary_empty_context():
    ctx = StudentContext()
    summary = ctx.safe_log_summary()
    assert summary["uses_profile"] is False
    assert summary["profile_completeness"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_student_context.py -v`
Expected: FAIL with `ImportError: cannot import name 'StudentContext'`

- [ ] **Step 3: Implement StudentContext**

```python
# src/dext_grounded/student_context.py
"""De-identified user background (grounded-generation spec §2.1).

Consumed by recommend/competition cores; never persisted to the fact layer
(catalog/Neo4j/Qdrant/knowledge-base). ``gpa_bucket`` and ``rank_bucket`` are
bucketed enums — raw GPA/rank values MUST NOT be accepted or logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StudentContext:
    education_stage: str | None = None
    school: str | None = None
    major: str | None = None
    gpa_bucket: str | None = None            # bucket enum, never raw GPA
    rank_bucket: str | None = None           # bucket enum, never raw rank
    research_interests: list[str] = field(default_factory=list)
    achievements_summary: str | None = None
    competition_experience_summary: str | None = None
    profile_completeness: float | None = None

    def __post_init__(self) -> None:
        # research_interests default must remain a fresh list per-instance
        if self.research_interests is None:
            object.__setattr__(self, "research_interests", [])

    def safe_log_summary(self) -> dict[str, object]:
        """Return a redacted dict safe for recommendation logs.

        Per spec §18: log only completeness bucket + whether profile was used,
        never raw profile text, GPA/rank buckets, or research-interest lists.
        """
        uses = any(
            v is not None and v != []
            for v in (
                self.education_stage, self.school, self.major, self.gpa_bucket,
                self.rank_bucket, self.achievements_summary,
                self.competition_experience_summary,
            )
        ) or bool(self.research_interests)
        return {
            "uses_profile": uses,
            "education_stage": self.education_stage,
            "profile_completeness": self.profile_completeness,
        }


__all__ = ["StudentContext"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.content import ContentClass
from dext_grounded.student_context import StudentContext

__all__ = ["ContentClass", "StudentContext"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_student_context.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/student_context.py src/dext_grounded/__init__.py tests/test_grounded_student_context.py
git commit -m "feat(grounded): add StudentContext with redacted log summary"
```

---

### Task 4: SourceRef + UserContextRef

**Files:**
- Create: `src/dext_grounded/source_ref.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_source_ref.py`

**Interfaces:**
- Produces: `SourceRef` frozen dataclass (spec §2.2: `doc_path`, `heading_path`, `chunk_hash`, `quote_or_summary`, `official_url`, `last_verified`); `UserContextRef` frozen dataclass (spec §4.2: `field`, `value_bucket`, `quote_or_summary`); `quote_or_summary` enforces a max length (default 500 chars, configurable).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_source_ref.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import SourceRef, UserContextRef


def test_source_ref_minimum_fields():
    ref = SourceRef(
        doc_path="catalog://entity/prof-001",
        heading_path="research_statements",
        chunk_hash="sha256:abc",
        quote_or_summary="works on retrieval-augmented generation",
    )
    assert ref.official_url is None
    assert ref.last_verified is None


def test_source_ref_is_frozen():
    ref = SourceRef(
        doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary="q",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.doc_path = "other"


def test_source_ref_quote_length_capped():
    long_quote = "x" * 600
    ref = SourceRef(
        doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary=long_quote,
    )
    assert len(ref.quote_or_summary) <= 500


def test_user_context_ref_fields():
    ref = UserContextRef(
        field="research_interests",
        value_bucket="advanced",
        quote_or_summary="interested in NLP and IR",
    )
    assert ref.field == "research_interests"
    assert ref.value_bucket == "advanced"


def test_user_context_ref_value_bucket_never_raw():
    # value_bucket is a redacted bucket; raw GPA/rank forbidden
    ref = UserContextRef(
        field="gpa_bucket", value_bucket="top10", quote_or_summary="high GPA",
    )
    assert ref.value_bucket == "top10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_source_ref.py -v`
Expected: FAIL with `ImportError: cannot import name 'SourceRef'`

- [ ] **Step 3: Implement SourceRef + UserContextRef**

```python
# src/dext_grounded/source_ref.py
"""Fact-citation reference units (grounded-generation spec §2.2, §4.2).

``SourceRef`` points to catalog/Neo4j evidence (recommend) or Markdown chunks
(competition). ``UserContextRef`` references a StudentContext field — it is NOT
a fact-bundle source; raw GPA/rank are never stored, only redacted buckets.
"""
from __future__ import annotations

from dataclasses import dataclass

QUOTE_MAX_LEN = 500


def _truncate(value: str, limit: int = QUOTE_MAX_LEN) -> str:
    return value[:limit]


@dataclass(frozen=True, slots=True)
class SourceRef:
    doc_path: str
    heading_path: str
    chunk_hash: str
    quote_or_summary: str
    official_url: str | None = None
    last_verified: str | None = None  # ISO-8601

    def __post_init__(self) -> None:
        object.__setattr__(self, "quote_or_summary", _truncate(self.quote_or_summary))


@dataclass(frozen=True, slots=True)
class UserContextRef:
    field: str                       # StudentContext field name
    value_bucket: str | None        # redacted bucket, never raw GPA/rank
    quote_or_summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "quote_or_summary", _truncate(self.quote_or_summary))


__all__ = ["SourceRef", "UserContextRef", "QUOTE_MAX_LEN"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.content import ContentClass
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "ContentClass",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_source_ref.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/source_ref.py src/dext_grounded/__init__.py tests/test_grounded_source_ref.py
git commit -m "feat(grounded): add SourceRef and UserContextRef"
```

---

### Task 5: FactBundle + FactItem

**Files:**
- Create: `src/dext_grounded/fact_bundle.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_fact_bundle.py`

**Interfaces:**
- Consumes: `SourceRef` (Task 4), `ContentClass` (Task 2).
- Produces: `FactItem` frozen dataclass (`field`, `value`, `content_class`, `source_refs`); `FactBundle` frozen dataclass (`build_id`, `subject_id`, `facts`, `source_refs`). `FactItem` with empty `source_refs` MUST have `content_class=UNCERTAIN` (enforced in `__post_init__`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_fact_bundle.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef


def _ref(doc: str = "catalog://e/p1") -> SourceRef:
    return SourceRef(
        doc_path=doc, heading_path="rs", chunk_hash="h", quote_or_summary="q",
    )


def test_fact_item_with_source_refs_is_fact():
    item = FactItem(
        field="research_statement",
        value="works on RAG",
        content_class=ContentClass.FACT,
        source_refs=[_ref()],
    )
    assert item.content_class == ContentClass.FACT


def test_fact_item_without_source_refs_must_be_uncertain():
    item = FactItem(
        field="research_statement",
        value="works on RAG",
        content_class=ContentClass.FACT,   # wrongly declared
        source_refs=[],
    )
    # post_init must force uncertain
    assert item.content_class == ContentClass.UNCERTAIN


def test_fact_item_is_frozen():
    item = FactItem(field="f", value="v", content_class=ContentClass.ADVICE, source_refs=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.value = "other"


def test_fact_bundle_is_immutable_snapshot():
    item = FactItem(
        field="eligibility", value="confirmed",
        content_class=ContentClass.FACT, source_refs=[_ref()],
    )
    bundle = FactBundle(
        build_id="build-1",
        subject_id="prof-001",
        facts=[item],
        source_refs=[_ref()],
    )
    assert bundle.build_id == "build-1"
    assert bundle.subject_id == "prof-001"
    assert len(bundle.facts) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.build_id = "build-2"


def test_fact_bundle_source_refs_lookup():
    r1 = _ref("catalog://e/p1")
    r2 = _ref("catalog://e/p2")
    bundle = FactBundle(
        build_id="b", subject_id="s", facts=[], source_refs=[r1, r2],
    )
    # source_refs is the canonical lookup set
    hashes = {ref.chunk_hash for ref in bundle.source_refs}
    assert hashes == {"h"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_fact_bundle.py -v`
Expected: FAIL with `ImportError: cannot import name 'FactBundle'`

- [ ] **Step 3: Implement FactBundle + FactItem**

```python
# src/dext_grounded/fact_bundle.py
"""Immutable fact bundle — the sole fact input to constrained generation
(grounded-generation spec §3).

Both recommend (ProfessorDetail) and competition (knowledge-base snippets)
assemble their own FactBundle but implement the same read-only interface.
LLM generation may ONLY consume ``facts``; it MUST NOT supplement from
training memory. A FactItem without source_refs MUST be marked uncertain.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded.content import ContentClass
from dext_grounded.source_ref import SourceRef


@dataclass(frozen=True, slots=True)
class FactItem:
    field: str                        # e.g. research_statement / eligibility / rule
    value: str
    content_class: ContentClass
    source_refs: list[SourceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_refs and self.content_class != ContentClass.UNCERTAIN:
            # spec §3: missing source_refs => must be uncertain
            object.__setattr__(self, "content_class", ContentClass.UNCERTAIN)


@dataclass(frozen=True, slots=True)
class FactBundle:
    build_id: str                     # recommend: ACTIVE build id; competition: kb version
    subject_id: str                   # recommend: entity_id; competition: competition_id
    facts: list[FactItem]
    source_refs: list[SourceRef]      # canonical lookup set for citation validation

    def __post_init__(self) -> None:
        if self.facts is None:
            object.__setattr__(self, "facts", [])
        if self.source_refs is None:
            object.__setattr__(self, "source_refs", [])


__all__ = ["FactBundle", "FactItem"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "ContentClass",
    "FactBundle",
    "FactItem",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_fact_bundle.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/fact_bundle.py src/dext_grounded/__init__.py tests/test_grounded_fact_bundle.py
git commit -m "feat(grounded): add FactBundle and FactItem with uncertain-on-no-refs"
```

---

### Task 6: Claim + GenerationResult + GenerationWarning

**Files:**
- Create: `src/dext_grounded/claims.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_claims.py`

**Interfaces:**
- Consumes: `ContentClass` (Task 2), `SourceRef` + `UserContextRef` (Task 4).
- Produces: `Claim` frozen dataclass (`text`, `content_class`, `fact_refs`, `user_context_ref`); `GenerationWarning` frozen dataclass (`code`, `message`, `claim_text` optional); `GenerationResult` frozen dataclass (`output`, `claims`, `cited_refs`, `warnings`). `Claim` with `content_class=FACT` and empty `fact_refs` is flagged at construction (returns a `fact_ref_missing` warning via `Claim.validate()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_claims.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import (
    Claim, ContentClass, GenerationResult, GenerationWarning, SourceRef, UserContextRef,
)


def _ref(doc: str = "catalog://e/p1") -> SourceRef:
    return SourceRef(doc_path=doc, heading_path="h", chunk_hash="c", quote_or_summary="q")


def test_claim_fact_requires_refs_or_flags_warning():
    claim = Claim(text="professor works on RAG", content_class=ContentClass.FACT, fact_refs=[])
    warnings = claim.validate()
    assert any(w.code == "fact_ref_missing" for w in warnings)


def test_claim_advice_with_user_context_ref_ok():
    claim = Claim(
        text="consider highlighting your NLP project",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    assert claim.validate() == []


def test_claim_uncertain_must_not_carry_fact_refs():
    claim = Claim(
        text="maybe accepts PhD students",
        content_class=ContentClass.UNCERTAIN,
        fact_refs=[_ref()],
    )
    warnings = claim.validate()
    assert any(w.code == "uncertain_claim_with_refs" for w in warnings)


def test_claim_is_frozen():
    claim = Claim(text="t", content_class=ContentClass.ADVICE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.text = "other"


def test_generation_result_defaults():
    res = GenerationResult(output="hello")
    assert res.claims == []
    assert res.cited_refs == []
    assert res.warnings == []
    assert res.output == "hello"


def test_generation_warning_minimum():
    w = GenerationWarning(code="fabricated_ref", message="made up")
    assert w.claim_text is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_claims.py -v`
Expected: FAIL with `ImportError: cannot import name 'Claim'`

- [ ] **Step 3: Implement Claim + GenerationResult + GenerationWarning**

```python
# src/dext_grounded/claims.py
"""Per-assertion claim classification + generation result (spec §4, §4.1).

Output is split into Claims, each independently classified and independently
cited, so mixed fact/advice/uncertain output is NOT collapsed into a single
global content_class. Validation produces structured warnings; the caller
(CitationValidator / SafetyGuard) decides downgrade vs. drop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded.content import ContentClass
from dext_grounded.source_ref import SourceRef, UserContextRef


@dataclass(frozen=True, slots=True)
class GenerationWarning:
    code: str
    message: str
    claim_text: str | None = None


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    content_class: ContentClass
    fact_refs: list[SourceRef] = field(default_factory=list)
    user_context_ref: UserContextRef | None = None

    def validate(self) -> list[GenerationWarning]:
        """Return warnings for this claim's citation shape (spec §5.2)."""
        warnings: list[GenerationWarning] = []
        if self.content_class == ContentClass.FACT and not self.fact_refs:
            warnings.append(GenerationWarning(
                code="fact_ref_missing",
                message="fact claim must carry non-empty fact_refs",
                claim_text=self.text,
            ))
        if self.content_class == ContentClass.UNCERTAIN and self.fact_refs:
            warnings.append(GenerationWarning(
                code="uncertain_claim_with_refs",
                message="uncertain claim must not carry fact_refs as if certain",
                claim_text=self.text,
            ))
        return warnings


@dataclass(frozen=True, slots=True)
class GenerationResult:
    output: dict | str         # dict when json_schema provided, else markdown
    claims: list[Claim] = field(default_factory=list)
    cited_refs: list[SourceRef] = field(default_factory=list)
    warnings: list[GenerationWarning] = field(default_factory=list)


__all__ = ["Claim", "GenerationResult", "GenerationWarning"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "GenerationResult",
    "GenerationWarning",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_claims.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/claims.py src/dext_grounded/__init__.py tests/test_grounded_claims.py
git commit -m "feat(grounded): add Claim, GenerationResult, GenerationWarning"
```

---

### Task 7: generation_profile_version + ProfileRegistry

**Files:**
- Create: `src/dext_grounded/profile.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_profile.py`

**Interfaces:**
- Produces: `GenerationProfile` frozen dataclass (`version`, `prompt_ids`, `json_schema_ids`, `safety_rule_ids`, `trim_token_budget`, `quote_max_len`); `ProfileRegistry` with `register(profile)` and `get(version)`; constructing a `GenerationProfile` with an empty `version` raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_profile.py
from __future__ import annotations

import pytest

from dext_grounded import GenerationProfile, ProfileRegistry


def test_generation_profile_minimum():
    p = GenerationProfile(version="gen-v1.0")
    assert p.version == "gen-v1.0"
    assert p.prompt_ids == []
    assert p.trim_token_budget > 0


def test_generation_profile_empty_version_rejected():
    with pytest.raises(ValueError):
        GenerationProfile(version="")


def test_registry_register_and_get():
    reg = ProfileRegistry()
    p = GenerationProfile(version="gen-v1.0", prompt_ids=["match-analysis-v1"])
    reg.register(p)
    assert reg.get("gen-v1.0") is p


def test_registry_unknown_version_returns_none():
    reg = ProfileRegistry()
    assert reg.get("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_profile.py -v`
Expected: FAIL with `ImportError: cannot import name 'GenerationProfile'`

- [ ] **Step 3: Implement GenerationProfile + ProfileRegistry**

```python
# src/dext_grounded/profile.py
"""Versioned generation profile (spec §7).

All prompts, JSON schemas, safety rules, trim thresholds, and token budgets
MUST enter ``generation_profile_version`` — peer to ranking_profile_version
(recommend) and competition_ranking_profile_version (competition). Version
changes require re-running groundedness evaluation; prompts must never be
edited inline in a handler.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GenerationProfile:
    version: str
    prompt_ids: list[str] = field(default_factory=list)
    json_schema_ids: list[str] = field(default_factory=list)
    safety_rule_ids: list[str] = field(default_factory=list)
    trim_token_budget: int = 4096
    quote_max_len: int = 500

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("generation profile version must be non-empty")


class ProfileRegistry:
    """In-process registry of known generation profiles."""

    def __init__(self) -> None:
        self._by_version: dict[str, GenerationProfile] = {}

    def register(self, profile: GenerationProfile) -> None:
        self._by_version[profile.version] = profile

    def get(self, version: str) -> GenerationProfile | None:
        return self._by_version.get(version)


__all__ = ["GenerationProfile", "ProfileRegistry"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.profile import GenerationProfile, ProfileRegistry
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "GenerationProfile",
    "GenerationResult",
    "GenerationWarning",
    "ProfileRegistry",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_profile.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/profile.py src/dext_grounded/__init__.py tests/test_grounded_profile.py
git commit -m "feat(grounded): add GenerationProfile and ProfileRegistry"
```

---

### Task 8: LLMGenerationPort protocol

**Files:**
- Create: `src/dext_grounded/ports.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_port_contract.py`

**Interfaces:**
- Consumes: `FactBundle` (Task 5), `StudentContext` (Task 3), `GenerationResult` (Task 6).
- Produces: `LLMGenerationPort` runtime-checkable Protocol with async `generate(system_prompt_id, user_inputs, fact_bundle, student_context, json_schema, generation_profile_version) -> GenerationResult`. Also an async `FakeLLMGenerationPort` for use by recommend/competition unit tests (returns a pre-set `GenerationResult`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_port_contract.py
from __future__ import annotations

import inspect

from dext_grounded import (
    FactBundle, FakeLLMGenerationPort, GenerationResult, LLMGenerationPort,
    StudentContext,
)


def test_llm_generation_port_is_protocol():
    assert hasattr(LLMGenerationPort, "_is_protocol") or LLMGenerationPort._is_runtime_protocol


def test_llm_generation_port_generate_signature():
    assert inspect.iscoroutinefunction(LLMGenerationPort.generate)
    sig = inspect.signature(LLMGenerationPort.generate)
    params = list(sig.parameters)
    # 'self' + the six spec params (§4)
    assert params[1:] == [
        "system_prompt_id", "user_inputs", "fact_bundle", "student_context",
        "json_schema", "generation_profile_version",
    ]


async def test_fake_llm_generation_port_returns_preset_result():
    preset = GenerationResult(output={"summary": "ok"})
    port = FakeLLMGenerationPort(preset)
    assert isinstance(port, LLMGenerationPort)
    result = await port.generate(
        system_prompt_id="match-analysis-v1",
        user_inputs={},
        fact_bundle=FactBundle(build_id="b", subject_id="s", facts=[], source_refs=[]),
        student_context=StudentContext(),
        json_schema={},
        generation_profile_version="gen-v1.0",
    )
    assert result is preset


async def test_fake_llm_generation_port_records_calls():
    preset = GenerationResult(output="hi")
    port = FakeLLMGenerationPort(preset)
    await port.generate(
        system_prompt_id="p1", user_inputs={"x": 1},
        fact_bundle=FactBundle(build_id="b", subject_id="s", facts=[], source_refs=[]),
        student_context=None, json_schema=None, generation_profile_version="v1",
    )
    assert len(port.calls) == 1
    assert port.calls[0]["system_prompt_id"] == "p1"
    assert port.calls[0]["generation_profile_version"] == "v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_port_contract.py -v`
Expected: FAIL with `ImportError: cannot import name 'LLMGenerationPort'`

- [ ] **Step 3: Implement LLMGenerationPort + FakeLLMGenerationPort**

```python
# src/dext_grounded/ports.py
"""LLM generation port — the constrained-generation protocol (spec §4).

``generate`` MUST run citation validation immediately after the LLM returns;
callers never receive un-validated output. The concrete LLM client lives in
each consumer module (recommend/competition); this package only fixes the
contract and a test fake.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from dext_grounded.claims import GenerationResult
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.student_context import StudentContext


@runtime_checkable
class LLMGenerationPort(Protocol):
    async def generate(
        self,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
    ) -> GenerationResult:
        ...


class FakeLLMGenerationPort:
    """Test double returning a preset result; records calls for assertions."""

    def __init__(self, preset: GenerationResult) -> None:
        self._preset = preset
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
    ) -> GenerationResult:
        self.calls.append(
            {
                "system_prompt_id": system_prompt_id,
                "user_inputs": user_inputs,
                "fact_bundle": fact_bundle,
                "student_context": student_context,
                "json_schema": json_schema,
                "generation_profile_version": generation_profile_version,
            }
        )
        return self._preset


__all__ = ["LLMGenerationPort", "FakeLLMGenerationPort"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.ports import FakeLLMGenerationPort, LLMGenerationPort
from dext_grounded.profile import GenerationProfile, ProfileRegistry
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "FakeLLMGenerationPort",
    "GenerationProfile",
    "GenerationResult",
    "GenerationWarning",
    "LLMGenerationPort",
    "ProfileRegistry",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_port_contract.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/ports.py src/dext_grounded/__init__.py tests/test_grounded_port_contract.py
git commit -m "feat(grounded): add LLMGenerationPort protocol and test fake"
```

---

### Task 9: CitationValidator

**Files:**
- Create: `src/dext_grounded/citation.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_citation.py`

**Interfaces:**
- Consumes: `Claim` + `GenerationResult` + `GenerationWarning` (Task 6), `FactBundle` (Task 5), `StudentContext` (Task 3), `SourceRef` + `UserContextRef` (Task 4).
- Produces: `CitationValidator` class with `validate(result, fact_bundle, student_context) -> GenerationResult`. Per spec §5.2: `fact` claims with `fact_refs` not in `fact_bundle.source_refs` → `fabricated_ref` + drop claim; `advice` claims with a `user_context_ref.field` not in the passed `StudentContext` → `fabricated_user_context` + drop the user-context ref; `uncertain` claims must not carry `fact_refs`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_citation.py
from __future__ import annotations

from dext_grounded import (
    Claim, CitationValidator, ContentClass, FactBundle, FactItem,
    GenerationResult, SourceRef, StudentContext, UserContextRef,
)


def _ref(doc: str, chunk: str = "c") -> SourceRef:
    return SourceRef(doc_path=doc, heading_path="h", chunk_hash=chunk, quote_or_summary="q")


def _bundle(refs: list[SourceRef]) -> FactBundle:
    return FactBundle(build_id="b", subject_id="s", facts=[], source_refs=refs)


def test_fact_claim_with_ref_in_bundle_kept():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[r])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert result.claims == [claim]
    assert result.warnings == []


def test_fact_claim_with_fabricated_ref_dropped():
    r_real = _ref("catalog://e/p1")
    r_fake = _ref("catalog://e/p2", chunk="nope")
    bundle = _bundle([r_real])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[r_fake])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert result.claims == []
    assert any(w.code == "fabricated_ref" for w in result.warnings)


def test_advice_claim_referencing_missing_student_field_drops_user_context():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(
        text="t",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    # StudentContext has NO research_interests set
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    # claim kept but user_context_ref stripped, warning emitted
    assert len(result.claims) == 1
    assert result.claims[0].user_context_ref is None
    assert any(w.code == "fabricated_user_context" for w in result.warnings)


def test_advice_claim_referencing_present_student_field_kept():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(
        text="t",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    ctx = StudentContext(research_interests=["NLP"])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, ctx,
    )
    assert result.claims[0].user_context_ref is not None
    assert result.warnings == []


def test_fact_claim_missing_refs_downgraded_to_uncertain():
    # fact claim with no refs at all → Claim.validate flags it, CitationValidator downgrades
    bundle = _bundle([_ref("catalog://e/p1")])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert any(w.code == "fact_ref_missing" for w in result.warnings)
    # downgraded to uncertain
    assert result.claims[0].content_class == ContentClass.UNCERTAIN


def test_all_claims_dropped_returns_no_grounded_output_marker():
    r_real = _ref("catalog://e/p1")
    bundle = _bundle([r_real])
    fake_claim = Claim(
        text="t", content_class=ContentClass.FACT, fact_refs=[_ref("catalog://e/p2", "x")],
    )
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[fake_claim]), bundle, StudentContext(),
    )
    assert result.claims == []
    assert any(w.code == "no_grounded_output" for w in result.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_citation.py -v`
Expected: FAIL with `ImportError: cannot import name 'CitationValidator'`

- [ ] **Step 3: Implement CitationValidator**

```python
# src/dext_grounded/citation.py
"""Per-claim citation validation (grounded-generation spec §5.2).

Runs immediately after LLM output. For each Claim, by content_class:
- fact: fact_refs must be non-empty AND every ref must be in the FactBundle's
  source_refs set; otherwise downgrade to uncertain (missing refs) or drop
  (fabricated refs).
- advice: may omit fact_refs; if it carries a user_context_ref, the field must
  be a present, non-empty field on the passed StudentContext, else strip the
  user_context_ref and warn fabricated_user_context.
- uncertain: must not carry fact_refs (would masquerade as certain).

When ALL claims are dropped, a no_grounded_output warning is emitted so the
caller never returns un-cited pure-LLM text as if grounded.
"""
from __future__ import annotations

from dataclasses import replace

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.student_context import StudentContext


def _bundle_ref_keys(bundle: FactBundle) -> set[tuple[str, str]]:
    return {(ref.doc_path, ref.chunk_hash) for ref in bundle.source_refs}


def _present_student_fields(student_context: StudentContext | None) -> set[str]:
    if student_context is None:
        return set()
    present: set[str] = set()
    for name in (
        "education_stage", "school", "major", "gpa_bucket", "rank_bucket",
        "achievements_summary", "competition_experience_summary",
    ):
        if getattr(student_context, name, None) not in (None, ""):
            present.add(name)
    if student_context.research_interests:
        present.add("research_interests")
    return present


class CitationValidator:
    def validate(
        self,
        result: GenerationResult,
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
    ) -> GenerationResult:
        valid_keys = _bundle_ref_keys(fact_bundle)
        present_fields = _present_student_fields(student_context)
        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []

        for claim in result.claims:
            warnings.extend(claim.validate())
            new_claim, claim_warnings = self._validate_claim(
                claim, valid_keys, present_fields,
            )
            warnings.extend(claim_warnings)
            if new_claim is not None:
                kept_claims.append(new_claim)

        if result.claims and not kept_claims:
            warnings.append(GenerationWarning(
                code="no_grounded_output",
                message="all claims dropped by citation validation; no grounded output",
            ))

        cited: list = []
        for claim in kept_claims:
            cited.extend(claim.fact_refs)
        # dedupe cited refs by (doc_path, chunk_hash) preserving order
        seen: set[tuple[str, str]] = set()
        unique_cited = []
        for ref in cited:
            key = (ref.doc_path, ref.chunk_hash)
            if key not in seen:
                seen.add(key)
                unique_cited.append(ref)

        return replace(
            result,
            claims=kept_claims,
            cited_refs=unique_cited,
            warnings=warnings,
        )

    def _validate_claim(
        self,
        claim: Claim,
        valid_keys: set[tuple[str, str]],
        present_fields: set[str],
    ) -> tuple[Claim | None, list[GenerationWarning]]:
        warnings: list[GenerationWarning] = []
        if claim.content_class == ContentClass.FACT:
            if not claim.fact_refs:
                # downgrade to uncertain (spec §5.2)
                warnings.append(GenerationWarning(
                    code="fact_ref_missing",
                    message="fact claim had no refs; downgraded to uncertain",
                    claim_text=claim.text,
                ))
                return replace(claim, content_class=ContentClass.UNCERTAIN), warnings
            fabricated = [
                r for r in claim.fact_refs
                if (r.doc_path, r.chunk_hash) not in valid_keys
            ]
            if fabricated:
                warnings.append(GenerationWarning(
                    code="fabricated_ref",
                    message=f"dropped claim with fabricated refs: {len(fabricated)}",
                    claim_text=claim.text,
                ))
                return None, warnings
            return claim, warnings

        if claim.content_class == ContentClass.ADVICE:
            if claim.user_context_ref is not None:
                field = claim.user_context_ref.field
                if field not in present_fields:
                    warnings.append(GenerationWarning(
                        code="fabricated_user_context",
                        message=f"stripped user_context_ref for absent field {field!r}",
                        claim_text=claim.text,
                    ))
                    return replace(claim, user_context_ref=None), warnings
            return claim, warnings

        # uncertain
        if claim.fact_refs:
            warnings.append(GenerationWarning(
                code="uncertain_claim_with_refs",
                message="uncertain claim carried fact_refs; stripped",
                claim_text=claim.text,
            ))
            return replace(claim, fact_refs=[]), warnings
        return claim, warnings


__all__ = ["CitationValidator"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.citation import CitationValidator
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.ports import FakeLLMGenerationPort, LLMGenerationPort
from dext_grounded.profile import GenerationProfile, ProfileRegistry
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "CitationValidator",
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "FakeLLMGenerationPort",
    "GenerationProfile",
    "GenerationResult",
    "GenerationWarning",
    "LLMGenerationPort",
    "ProfileRegistry",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_citation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/citation.py src/dext_grounded/__init__.py tests/test_grounded_citation.py
git commit -m "feat(grounded): add CitationValidator with per-claim downgrade/drop"
```

---

### Task 10: SafetyGuard

**Files:**
- Create: `src/dext_grounded/safety.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/test_grounded_safety.py`

**Interfaces:**
- Consumes: `GenerationResult` + `Claim` + `GenerationWarning` (Task 6), `ContentClass` (Task 2).
- Produces: `SafetyGuard` class with `inspect(result, domain) -> GenerationResult` where `domain` is `"recommend"|"competition"|"generic"`. Per spec §6 table: probability claims → drop claim + `no_probability_claim` warning; advice violations (代做/挂名/伪造数据/赛中泄题/绕过查重/规避AI披露) → `unsafe_advice` error + reject whole output; unauthorized contact → `unauthorized_contact` warning + strip; stale fact presented as current → downgrade to uncertain + `stale_fact`. Recommend-side extra: no admission/保研/导师接收意愿. Competition-side extra: 2024 report dir as 教育部白名单 → reject; 往届 as 当届 → downgrade uncertain.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounded_safety.py
from __future__ import annotations

from dext_grounded import (
    Claim, ContentClass, GenerationResult, SafetyGuard,
)


def test_probability_claim_dropped():
    claim = Claim(
        text="录取概率 80%", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="recommend",
    )
    assert res.claims == []
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_unsafe_advice_rejects_whole_output():
    claim = Claim(
        text="我可以代做这个项目", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="competition",
    )
    assert res.claims == []
    assert any(w.code == "unsafe_advice" and w.message == "ERROR" for w in res.warnings)


def test_unauthorized_contact_stripped():
    # contact embedded in output text — guard flags unauthorized_contact
    res = SafetyGuard().inspect(
        GenerationResult(output="email: foo@bar.com", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert any(w.code == "unauthorized_contact" for w in res.warnings)


def test_stale_fact_downgraded_to_uncertain():
    claim = Claim(
        text="2024 年报名时间是 3 月", content_class=ContentClass.FACT,
        # fact_refs omitted → already uncertain at Claim level, but guard should
        # still detect "stale" wording when presented as current.
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="competition",
    )
    assert any(w.code == "stale_fact" for w in res.warnings)
    assert res.claims[0].content_class == ContentClass.UNCERTAIN


def test_recommend_blocks_admission_probability():
    claim = Claim(
        text="导师接收你的意愿较高", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="recommend",
    )
    assert res.claims == []
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_competition_2024_dir_as_whitelist_rejected():
    claim = Claim(
        text="这是教育部白名单赛事", content_class=ContentClass.FACT,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="2024 竞赛分析报告目录 教育部白名单", claims=[claim]),
        domain="competition",
    )
    assert any(w.code == "unsafe_advice" and w.message == "ERROR" for w in res.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grounded_safety.py -v`
Expected: FAIL with `ImportError: cannot import name 'SafetyGuard'`

- [ ] **Step 3: Implement SafetyGuard**

```python
# src/dext_grounded/safety.py
"""Safety guard — rule-based output inspection (spec §6).

Runs AFTER CitationValidator. Rules:
- probability claim (录取/保研/奖学金/综测加分/Offer/获奖/导师接收意愿):
  drop claim + no_probability_claim warning.
- unsafe advice (代做/挂名/伪造数据/赛中泄题/绕过查重/规避AI披露):
  reject whole output + unsafe_advice ERROR.
- unauthorized contact (email/phone when include_contacts=False):
  strip + unauthorized_contact warning.
- stale fact (往届时间/奖项比例/赛道/费用/AI规则当成当届): downgrade uncertain + stale_fact.

Domain extras:
- recommend: blocks admission/保研/导师接收意愿 (subset of probability rule).
- competition: "2024 竞赛分析报告目录" written as "教育部白名单" → unsafe_advice reject.
"""
from __future__ import annotations

import re
from dataclasses import replace

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass

_DOMAINS = {"recommend", "competition", "generic"}

_PROBABILITY_PATTERNS = [
    "录取概率", "保研概率", "奖学金概率", "综测加分", "Offer 概率", "获奖概率",
    "导师接收意愿", "导师接收你的意愿", "录用意愿", "导师愿意接收",
]
_UNSAFE_ADVICE_PATTERNS = [
    "代做", "挂名", "伪造数据", "赛中泄题", "绕过查重", "规避 AI 披露", "规避AI披露",
]
_CONTACT_PATTERNS = [
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),  # email
    re.compile(r"1[3-9]\d{9}"),                                       # CN mobile
]
_STALE_PATTERNS = [
    "2024 年报名", "2024年报名", "往届", "去年", "上一届",
]
_COMPETITION_WHITELIST_FAKE = "教育部白名单"
_COMPETITION_REPORT_DIR = "2024 竞赛分析报告目录"


class SafetyGuard:
    def inspect(
        self,
        result: GenerationResult,
        *,
        domain: str = "generic",
        include_contacts: bool = False,
    ) -> GenerationResult:
        if domain not in _DOMAINS:
            raise ValueError(f"unknown safety domain: {domain!r}")

        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []
        output_text = result.output if isinstance(result.output, str) else ""

        # competition: fake-whitelist reject fires on output text first
        if domain == "competition" and _COMPETITION_REPORT_DIR in output_text \
                and _COMPETITION_WHITELIST_FAKE in output_text:
            warnings.append(GenerationWarning(
                code="unsafe_advice", message="ERROR",
                claim_text="2024 report dir mislabelled as 教育部白名单",
            ))
            return replace(result, claims=[], warnings=warnings)

        for claim in result.claims:
            text = claim.text
            if any(p in text for p in _UNSAFE_ADVICE_PATTERNS):
                warnings.append(GenerationWarning(
                    code="unsafe_advice", message="ERROR", claim_text=text,
                ))
                return replace(result, claims=[], warnings=warnings)
            if any(p in text for p in _PROBABILITY_PATTERNS):
                warnings.append(GenerationWarning(
                    code="no_probability_claim", message="dropped probability claim",
                    claim_text=text,
                ))
                continue
            if domain == "competition" and any(p in text for p in _STALE_PATTERNS) \
                    and claim.content_class == ContentClass.FACT:
                warnings.append(GenerationWarning(
                    code="stale_fact", message="downgraded stale fact to uncertain",
                    claim_text=text,
                ))
                kept_claims.append(replace(claim, content_class=ContentClass.UNCERTAIN))
                continue
            kept_claims.append(claim)

        if not include_contacts and isinstance(result.output, str):
            for pattern in _CONTACT_PATTERNS:
                if pattern.search(result.output):
                    warnings.append(GenerationWarning(
                        code="unauthorized_contact",
                        message="output contained contact info without include_contacts",
                    ))
                    break

        return replace(result, claims=kept_claims, warnings=warnings)


__all__ = ["SafetyGuard"]
```

Update `__init__.py`:

```python
# src/dext_grounded/__init__.py
from dext_grounded.citation import CitationValidator
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.ports import FakeLLMGenerationPort, LLMGenerationPort
from dext_grounded.profile import GenerationProfile, ProfileRegistry
from dext_grounded.safety import SafetyGuard
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "CitationValidator",
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "FakeLLMGenerationPort",
    "GenerationProfile",
    "GenerationResult",
    "GenerationWarning",
    "LLMGenerationPort",
    "ProfileRegistry",
    "QUOTE_MAX_LEN",
    "SafetyGuard",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grounded_safety.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/safety.py src/dext_grounded/__init__.py tests/test_grounded_safety.py
git commit -m "feat(grounded): add SafetyGuard with probability/unsafe/contact/stale rules"
```

---

### Task 10.5: Versioned rules module + trimmer hook + eval contract

**Files:**
- Create: `src/dext_grounded/rules.py`, `src/dext_grounded/rules/grounded_v1.yaml`
- Create: `src/dext_grounded/trim.py`, `src/dext_grounded/eval.py`
- Modify: `src/dext_grounded/__init__.py`
- Test: `tests/dext_grounded/test_grounded_rules.py`, `tests/dext_grounded/test_grounded_trim.py`, `tests/dext_grounded/test_grounded_eval.py`

**Interfaces:**
- Produces: `GroundedRules`/`load_grounded_rules()` (versioned YAML rules carrying `trim_token_budget`, `quote_max_len`, safety patterns, `student_context_fields`, `manifest_hash`); `trim(fact_bundle, token_budget, query_terms) -> FactBundle` (pure, same-type; keeps query-hit `FactItem`s, never drops `source_refs`, rejects contact-bearing items); `eval.describe_metrics()` + `eval.load_acceptance_samples()` (precision / no-probability-claim rate definitions + version-bound sample loader).

> **Reproducibility:** `rules.py` + `rules/grounded_v1.yaml` are load-bearing — `source_ref.py` calls `load_grounded_rules()` at import. They MUST be committed (`git add`) here, not left untracked.

- [ ] **Step 1: Write the failing tests** (rules parse + version/hash; trim keeps hits+refs and rejects contacts; eval exposes metrics + version-bound samples)
- [ ] **Step 2: Run → FAIL** (`ImportError: cannot import name 'trim'` / `load_grounded_rules`)
- [ ] **Step 3: Implement** `rules.py` + yaml, `trim.py`, `eval.py`; re-export `trim` and `load_grounded_rules` from `__init__.py`
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git add src/dext_grounded/rules.py src/dext_grounded/rules/grounded_v1.yaml \
        src/dext_grounded/trim.py src/dext_grounded/eval.py src/dext_grounded/__init__.py \
        tests/dext_grounded/test_grounded_rules.py tests/dext_grounded/test_grounded_trim.py \
        tests/dext_grounded/test_grounded_eval.py
git commit -m "feat(grounded): add versioned rules, trimmer hook, eval contract"
```

---

### Task 11: Full grounded package green + spec §9 acceptance

**Files:**
- Test: `tests/dext_grounded/test_grounded_acceptance.py` (append attack/immutability/trimming/eval tests)

**Interfaces:**
- Consumes: all of Tasks 1–10.

- [ ] **Step 1: Write the acceptance test (spec §9)**

> **Reviewer-driven strengthening (2026-07-01):** the original acceptance test only asserted `claims` + warning codes, never the final `output`. Spec §6 now requires `SafetyGuard` to sanitize `output`, not just claims. Spec §9 now requires **attack tests on `output`**, **deep immutability**, **bucket validation**, **trimmer hook**, and an **eval contract** (precision/no-probability-claim metrics + version-bound samples). Append the tests below; do not weaken the docstring's "FactBundle trimming hook" claim — make it true.

```python
# tests/dext_grounded/test_grounded_acceptance.py — append
import dataclasses
import pytest

from dext_grounded import (
    Claim, CitationValidator, ContentClass, FactBundle, FactItem,
    GenerationResult, SafetyGuard, SourceRef, StudentContext, UserContextRef,
)


# ---- §6 attack tests: final output must be sanitized, not just claims ----

def test_safety_probability_claim_removed_from_output():
    claim = Claim(text="录取概率 90%", content_class=ContentClass.ADVICE)
    res = SafetyGuard().inspect(
        GenerationResult(output="录取概率 90%", claims=[claim]),
        domain="recommend", include_contacts=False,
    )
    assert "录取概率" not in (res.output if isinstance(res.output, str) else "")
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_safety_unsafe_advice_blanks_output():
    claim = Claim(text="我可以代做这个项目", content_class=ContentClass.ADVICE)
    res = SafetyGuard().inspect(
        GenerationResult(output="我可以代做这个项目", claims=[claim]),
        domain="competition", include_contacts=False,
    )
    # output must NOT still carry the unsafe advice verbatim
    assert "代做" not in (res.output if isinstance(res.output, str) else "")
    assert any(w.code == "unsafe_advice" for w in res.warnings)


def test_safety_unauthorized_contact_stripped_from_output():
    res = SafetyGuard().inspect(
        GenerationResult(output="联系：foo@bar.com 或 13800000000", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert "foo@bar.com" not in res.output
    assert "13800000000" not in res.output
    assert any(w.code == "unauthorized_contact" for w in res.warnings)


# ---- §5.2 advice fact_refs + canonical identity ----

def test_advice_with_fabricated_fact_ref_dropped():
    real = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="works on RAG")
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[], source_refs=[real])
    fake = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="FAKED SUMMARY")  # same key, faked quote
    claim = Claim(text="advice grounded in RAG", content_class=ContentClass.ADVICE,
                  fact_refs=[fake])
    res = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert all(r.quote_or_summary != "FAKED SUMMARY" for r in res.cited_refs)
    assert any(w.code == "fabricated_ref" for w in res.warnings)


# ---- §3 deep immutability ----

def test_fact_bundle_facts_is_readonly():
    bundle = FactBundle(
        build_id="b", subject_id="s", facts=[
            FactItem(field="f", value="v", content_class=ContentClass.UNCERTAIN,
                     source_refs=[]),
        ], source_refs=[],
    )
    with pytest.raises((AttributeError, TypeError)):
        bundle.facts.append(FactItem(field="x", value="y",
                                     content_class=ContentClass.UNCERTAIN, source_refs=[]))


def test_claim_fact_refs_is_readonly():
    ref = SourceRef(doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary="q")
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[ref])
    with pytest.raises((AttributeError, TypeError)):
        claim.fact_refs.append(ref)


# ---- §2.1 bucket validation + redacted log summary ----

def test_student_context_rejects_raw_gpa():
    with pytest.raises(ValueError):
        StudentContext(gpa_bucket="3.97")


def test_student_context_safe_log_summary_buckets_completeness():
    ctx = StudentContext(profile_completeness=0.8)
    summary = ctx.safe_log_summary()
    # raw float must not leak
    assert "profile_completeness" not in summary or not isinstance(
        summary.get("profile_completeness"), float)
    assert "completeness_bucket" in summary  # coarse bucket, not raw float


# ---- §5.1 trimmer hook ----

def test_trim_keeps_query_hits_and_source_refs():
    from dext_grounded import trim  # NEW: R0 must expose the trim hook
    real = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="works on RAG")
    hit = FactItem(field="research_statement", value="RAG",
                   content_class=ContentClass.FACT, source_refs=[real])
    miss = FactItem(field="bio", value="x" * 10000,
                    content_class=ContentClass.UNCERTAIN, source_refs=[])
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[hit, miss],
                        source_refs=[real])
    trimmed = trim(bundle, token_budget=128, query_terms={"RAG"})
    assert any(f.field == "research_statement" for f in trimmed.facts)
    assert all(f.source_refs for f in trimmed.facts if f.content_class == ContentClass.FACT)


# ---- §9 eval contract (precision / no-probability-claim / version binding) ----

def test_eval_contract_samples_bind_profile_version():
    from dext_grounded.eval import describe_metrics, load_acceptance_samples
    metrics = describe_metrics()
    assert "grounded_precision" in metrics and "no_probability_claim_rate" in metrics
    samples = load_acceptance_samples()
    assert samples  # non-empty built-in set
    for s in samples:
        assert s.generation_profile_version
        assert s.grounded_rules_manifest_hash
```

- [ ] **Step 2: Run the whole grounded test suite**

Run: `uv run pytest tests/dext_grounded/ -q`
Expected: PASS (single-module green — full-project `pytest -q` is NOT required; known timeout)

- [ ] **Step 3: Commit**

```bash
git add tests/dext_grounded/test_grounded_acceptance.py
git commit -m "test(grounded): add §9 attack/immutability/trimming/eval acceptance tests"
```

> **Reproducibility gate (must):** `src/dext_grounded/rules.py`, `src/dext_grounded/rules/grounded_v1.yaml`, and `tests/dext_grounded/test_grounded_rules.py` are load-bearing (`source_ref.py` calls `load_grounded_rules()` at import). They MUST be `git add`-ed in their respective tasks; committing only the tracked files breaks `import dext_grounded`.

---

## Self-Review

**1. Spec coverage** (grounded-generation-design §1–§9):
- §2.1 StudentContext → Task 3 ✓ (now incl. raw-value rejection in `__post_init__` + bucketed `safe_log_summary`)
- §2.2 SourceRef → Task 4 ✓
- §2.3 ContentClass → Task 2 ✓
- §3 FactBundle/FactItem (uncertain-on-no-refs, **deep immutability**) → Task 5 ✓
- §4 LLMGenerationPort + §4.1 Claim (per-claim, no global content_class) → Tasks 6, 8 ✓
- §4.2 UserContextRef → Task 4 ✓
- §5.1 trimming → R0 lands the **trim hook + budget field** (`trim(fact_bundle, token_budget, query_terms)` + `GroundedRules.trim_token_budget`); full tokenizer/strategy deferred to R6 — covered as a contract + acceptance test ✓
- §5.2 CitationValidator (now incl. **advice `fact_refs` validation** + **canonical ref identity** by `(doc_path, heading_path, chunk_hash)` + content hash, not just `(doc_path, chunk_hash)`) → Task 9 ✓
- §6 SafetyGuard (now incl. **`output` sanitization**, not just claims) → Task 10 ✓
- §7 generation_profile_version → Task 7 ✓
- §8 failure modes (`no_grounded_output`, `fabricated_ref`, `fabricated_user_context`) → Tasks 9, 11 ✓
- §9 acceptance (import boundary, per-claim classification, grounded precision markers, **attack tests on `output`**, **deep immutability**, **bucket validation**, **trimmer hook**, **eval contract**, **single-module green**) → Tasks 1, 11 ✓

**2. Placeholder scan:** No TBD/TODO; every code step has full source. Warning codes are enumerated, not "add appropriate handling".

**3. Type consistency:** `Claim.validate()` returns `list[GenerationWarning]` (Task 6) — used consistently in Task 9. `FactBundle.source_refs` is the canonical lookup set (Task 5) — used by `CitationValidator` (Task 9) via `_bundle_ref_keys`. `UserContextRef.field` (Task 4) — checked against `StudentContext` present fields (Task 9). `ContentClass.UNCERTAIN` downgrade path (Task 9) — matches Task 5's `FactItem` uncertain enforcement. `GenerationResult.output: dict | str` (Task 6) — SafetyGuard (Task 10) guards `isinstance(str)` before contact regex. `LLMGenerationPort.generate` signature (Task 8) — matches the test fake and acceptance test (Task 11).

**Execution note:** This plan produces a pure contract layer with zero LLM calls — fully unblocked from the upstream ACTIVE-build gate. It lands the shared prerequisite for both `dext_recommend` (phase 1 + 6) and `dext_competition`.
