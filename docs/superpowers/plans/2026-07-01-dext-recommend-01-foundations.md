# dext_recommend Foundations Implementation Plan (R1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `dext_recommend` package skeleton — internal models, error codes, config, five ports (with the explicit-snapshot contract), four fake ports, and an import-boundary test — so that phases R2–R6 can develop all core logic against fakes with zero real external services.

**Architecture:** A 4th peer package `src/dext_recommend/` mirrors `dext_graph`'s settings/dataclass conventions. Models re-export `StudentContext`/`SourceRef` from the shared `dext_grounded` contract (built in R0) rather than redefining them. Each port is a `Protocol` whose data methods take an `ActiveBuildSnapshot` as an explicit parameter — pinning one snapshot per request is an interface contract, not a convention. `core/service.py` is a thin placeholder orchestrator; real pipeline lands in R3.

**Tech Stack:** Python 3.11, pydantic-settings v2 (config), stdlib `dataclasses`/`Protocol`, pytest (asyncio_mode="auto"). Depends on `dext_grounded` (R0).

## Global Constraints

Copied verbatim from recommendation overview §3, §6.1, §8, §17 and foundations §5, §8:
- `dext_recommend` 与 `dext`、`dext_graph`、`dext_monitor` 平级，不 import 这三者任何内部模块、ORM model、service class 或 CLI command。
- `dext_recommend` 与 `dext_competition` 互不 import，但都可 import 共享 grounded-generation 契约。
- snapshot 显式入参：`hybrid_recall`/`hydrate`/`get_detail`/`alias_readback`/`count_readback`/`embed` 都显式接收 `ActiveBuildSnapshot`。
- 端口不得在内部自行 `get_snapshot()` 或 `refresh()`；`refresh()` 失败返回 `null`。
- 一次推荐请求、详情请求、匹配分析、套磁邮件或对比请求只能使用同一个 snapshot。
- `StudentContext`、`SourceRef` 从共享契约 import，不重复定义。
- 字段名与 overview §8、§10 严格一致。
- API key 只从环境读取，不进入配置对象的可序列化表示、日志或 manifest。
- 配置 key 命名稳定，API key 不出现在任何可序列化结构中。
- KISS；prefer many small focused modules; each `__init__.py` re-exports public interface (`__all__`); modules form an acyclic import DAG.
- 平台 Windows + Git Bash；`LF will be replaced by CRLF` git warnings are benign.

---

## File Structure

```text
src/dext_recommend/
  __init__.py              # re-export public surface, __all__
  config.py               # RecommendSettings (pydantic-settings)
  models.py               # RecommendRequest/Response/RecommendedProfessor/etc.
  errors.py               # RecommendationError + error code enum
  readiness.py            # ActiveBuildSnapshot (immutable) + ReadinessService placeholder
  ports/
    __init__.py            # re-export ports
    build_snapshot.py      # BuildSnapshotPort + FakeBuildSnapshotPort
    vector_search.py       # VectorSearchPort + VectorHit + AliasReadback + FakeVectorSearchPort
    professor_facts.py     # ProfessorFactPort + ProfessorFact + ProfessorDetail + FakeProfessorFactPort
    embedding.py           # QueryEmbeddingPort + EmbeddingResult + FakeQueryEmbeddingPort
    generation.py          # re-export LLMGenerationPort from dext_grounded
  adapters/
    __init__.py            # adapter names declared; implementations land in R2+
  core/
    __init__.py
    service.py             # RecommendRequest -> RecommendResponse placeholder orchestrator
  facts/__init__.py
  generation/__init__.py
  eval/__init__.py
tests/
  test_recommend_import_boundary.py
  test_recommend_models.py
  test_recommend_errors.py
  test_recommend_config.py
  test_recommend_ports_snapshot_contract.py
  test_recommend_fake_ports.py
```

**Responsibilities:** `config.py` (env-driven settings, secrets `repr=False`), `models.py` (frozen dataclasses mirroring overview §8/§10), `errors.py` (structured error + code enum), `readiness.py` (`ActiveBuildSnapshot` frozen dataclass + `ReadinessService` placeholder; real logic in R2), `ports/` (5 protocols + 4 fakes + their helper dataclasses), `core/service.py` (placeholder). Acyclic DAG: `__init__` → `models`/`errors`/`config` → `readiness` → `ports` → `core`; `models` imports `dext_grounded` (peer). Nothing imports `dext`/`dext_graph`/`dext_monitor`/`dext_competition`.

---

### Task 1: Package skeleton + import boundary test

**Files:**
- Create: `src/dext_recommend/__init__.py`
- Create: `src/dext_recommend/ports/__init__.py`, `adapters/__init__.py`, `core/__init__.py`, `facts/__init__.py`, `generation/__init__.py`, `eval/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_recommend_import_boundary.py`

**Interfaces:**
- Produces: package `dext_recommend` importable; `__all__` empty for now.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_import_boundary.py
"""dext_recommend must not import dext / dext_graph / dext_monitor / dext_competition."""
from __future__ import annotations

import importlib
import sys


def test_dext_recommend_is_importable():
    mod = importlib.import_module("dext_recommend")
    assert mod.__name__ == "dext_recommend"


def test_dext_recommend_does_not_import_dext_family():
    for name in list(sys.modules):
        if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            del sys.modules[name]
    importlib.import_module("dext_recommend")
    for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
        assert forbidden not in sys.modules, (
            f"dext_recommend must not import peer module {forbidden!r}"
        )


def test_dext_recommend_imports_grounded_contract():
    # it MAY import dext_grounded (shared contract)
    importlib.import_module("dext_recommend")
    assert "dext_grounded" in sys.modules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_import_boundary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend'`

- [ ] **Step 3: Create the package + subpackages**

```python
# src/dext_recommend/__init__.py
"""dext_recommend — explainable mentor recommendation query layer.

Peer package to dext / dext_graph / dext_monitor. Read-only consumer of
published ACTIVE build artifacts (catalog SQLite, Qdrant current alias,
Neo4j active pointer). Never writes back, never triggers build/promote.

Imports the shared dext_grounded contract for StudentContext/SourceRef/
LLMGenerationPort, but never imports dext/dext_graph/dext_monitor/
dext_competition internals.
"""

__version__ = "0.1.0"

__all__: list[str] = []
```

```python
# src/dext_recommend/ports/__init__.py
"""Port protocols (interfaces to published build artifacts)."""
__all__: list[str] = []
```

```python
# src/dext_recommend/adapters/__init__.py
"""Concrete adapter implementations (filled in R2+)."""
__all__: list[str] = []
```

```python
# src/dext_recommend/core/__init__.py
"""Recommendation pipeline orchestration."""
__all__: list[str] = []
```

```python
# src/dext_recommend/facts/__init__.py
"""Professor fact-bundle assembly (R4)."""
__all__: list[str] = []
```

```python
# src/dext_recommend/generation/__init__.py
"""Constrained generation services (R6)."""
__all__: list[str] = []
```

```python
# src/dext_recommend/eval/__init__.py
"""Offline evaluation harness."""
__all__: list[str] = []
```

Modify `pyproject.toml`:

```toml
# [tool.hatch.build.targets.wheel] packages
packages = ["src/dext", "src/dext_graph", "src/dext_monitor", "src/dext_grounded", "src/dext_recommend"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_import_boundary.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend pyproject.toml tests/test_recommend_import_boundary.py
git commit -m "feat(rec): scaffold dext_recommend peer package"
```

---

### Task 2: RecommendSettings (config)

**Files:**
- Create: `src/dext_recommend/config.py`
- Modify: `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_config.py`

**Interfaces:**
- Produces: `RecommendSettings(BaseSettings)` with `env_prefix="DEXT_RECOMMEND_"`, fields from foundations §7, secrets `repr=False`, `safe_snapshot()` excludes secrets.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_config.py
from __future__ import annotations

from pathlib import Path

from dext_recommend import RecommendSettings


def test_recommend_settings_defaults():
    s = RecommendSettings()
    assert s.qdrant_alias == "dext_professors_current"
    assert s.total_timeout == 30
    assert s.oversample_default == 200
    assert s.oversample_max == 1000


def test_recommend_settings_safe_snapshot_excludes_api_keys():
    s = RecommendSettings(
        embedding_api_key="secret-embed",
        llm_api_key="secret-llm",
    )
    snap = s.safe_snapshot()
    assert "secret-embed" not in str(snap)
    assert "secret-llm" not in str(snap)
    assert "embedding_api_key" not in snap
    assert "llm_api_key" not in snap
    # but operational fields are present
    assert snap["qdrant_alias"] == "dext_professors_current"


def test_recommend_settings_paths_are_path_objects():
    s = RecommendSettings()
    assert isinstance(s.catalog_path, Path)
    assert isinstance(s.build_manifest_path, Path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'RecommendSettings'`

- [ ] **Step 3: Implement RecommendSettings**

```python
# src/dext_recommend/config.py
"""Configuration for dext_recommend; loaded from env / .env / defaults.

All timeouts, limits, and ranking/generation profile paths are configurable.
API keys are read from env only, never serialized into the config object's
loggable representation, the manifest, or logs (overview §6.6, §18).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RecommendSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_RECOMMEND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Published-artifact locations
    build_manifest_path: Path = Path("data/catalog/build-manifest.json")
    catalog_path: Path = Path("data/catalog/catalog.db")
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_alias: str = "dext_professors_current"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_database: str = "neo4j"
    neo4j_username: str = ""
    neo4j_password: SecretStr = Field(default_factory=SecretStr)

    # Embedding (must align with ACTIVE build fingerprint)
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_api_key: SecretStr = Field(default_factory=SecretStr)

    # LLM (constrained generation)
    llm_api_key: SecretStr = Field(default_factory=SecretStr)
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"

    # Profile paths
    ranking_profile_path: Path = Path("data/recommend/ranking-profile.json")
    generation_profile_path: Path = Path("data/recommend/generation-profile.json")

    # Timeouts / limits (overview §6.6, §11)
    total_timeout: float = 30.0
    oversample_default: int = 200
    oversample_max: int = 1000

    def safe_snapshot(self) -> dict[str, object]:
        # SecretStr already redacts in model_dump; safe_snapshot additionally
        # excludes the key fields entirely for log/manifest use.
        return self.model_dump(
            mode="json",
            exclude={"embedding_api_key", "llm_api_key", "neo4j_password"},
        )


__all__ = ["RecommendSettings"]
```

Update `__init__.py`:

```python
# src/dext_recommend/__init__.py
from dext_recommend.config import RecommendSettings

__all__ = ["RecommendSettings"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/config.py src/dext_recommend/__init__.py tests/test_recommend_config.py
git commit -m "feat(rec): add RecommendSettings with secret-safe snapshot"
```

---

### Task 3: Errors + error code enum

**Files:**
- Create: `src/dext_recommend/errors.py`
- Modify: `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_errors.py`

**Interfaces:**
- Produces: `ErrorSeverity` enum (`WARNING`, `ERROR`); `RecommendationErrorCode` enum (the 8 codes from foundations §4); `RecommendationError` frozen dataclass (`code`, `severity`, `message`, `build_id`, `retryable`, `operator_action`, `user_action`) matching overview §17.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_errors.py
from __future__ import annotations

import dataclasses

import pytest

from dext_recommend import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)


def test_error_code_enum_has_all_foundations_codes():
    codes = {c.value for c in RecommendationErrorCode}
    assert codes == {
        "active_build_unavailable", "active_build_inconsistent",
        "embedding_fingerprint_mismatch", "no_candidates_after_filters",
        "payload_prefilter_degraded", "insufficient_facts",
        "unauthorized_contact", "generation_unavailable",
    }


def test_recommendation_error_minimum():
    err = RecommendationError(
        code=RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
        severity=ErrorSeverity.ERROR,
        message="no ACTIVE build",
    )
    assert err.build_id is None
    assert err.retryable is False
    assert err.operator_action is None
    assert err.user_action is None


def test_recommendation_error_is_frozen():
    err = RecommendationError(
        code=RecommendationErrorCode.NO_CANDIDATES_AFTER_FILTERS,
        severity=ErrorSeverity.WARNING,
        message="empty",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        err.message = "other"


def test_recommendation_error_to_dict_for_response():
    err = RecommendationError(
        code=RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
        severity=ErrorSeverity.ERROR,
        message="fp mismatch",
        build_id="b-1",
        retryable=False,
        operator_action="rebuild vectors",
        user_action="try again later",
    )
    d = err.to_dict()
    assert d["code"] == "embedding_fingerprint_mismatch"
    assert d["severity"] == "error"
    assert d["build_id"] == "b-1"
    assert d["retryable"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_errors.py -v`
Expected: FAIL with `ImportError: cannot import name 'ErrorSeverity'`

- [ ] **Step 3: Implement errors**

```python
# src/dext_recommend/errors.py
"""Structured errors + warnings (overview §17, foundations §4).

Every drop/skip/retry carries a reason code + count. Errors never crash the
process; they are returned as structured values. Codes are registered here
up-front; later phases wire the trigger logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class RecommendationErrorCode(str, Enum):
    ACTIVE_BUILD_UNAVAILABLE = "active_build_unavailable"
    ACTIVE_BUILD_INCONSISTENT = "active_build_inconsistent"
    EMBEDDING_FINGERPRINT_MISMATCH = "embedding_fingerprint_mismatch"
    NO_CANDIDATES_AFTER_FILTERS = "no_candidates_after_filters"
    PAYLOAD_PREFILTER_DEGRADED = "payload_prefilter_degraded"
    INSUFFICIENT_FACTS = "insufficient_facts"
    UNAUTHORIZED_CONTACT = "unauthorized_contact"
    GENERATION_UNAVAILABLE = "generation_unavailable"


@dataclass(frozen=True, slots=True)
class RecommendationError:
    code: RecommendationErrorCode
    severity: ErrorSeverity
    message: str
    build_id: str | None = None
    retryable: bool = False
    operator_action: str | None = None
    user_action: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "build_id": self.build_id,
            "retryable": self.retryable,
            "operator_action": self.operator_action,
            "user_action": self.user_action,
        }


__all__ = ["ErrorSeverity", "RecommendationError", "RecommendationErrorCode"]
```

Update `__init__.py`:

```python
# src/dext_recommend/__init__.py
from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)

__all__ = [
    "ErrorSeverity",
    "RecommendSettings",
    "RecommendationError",
    "RecommendationErrorCode",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_errors.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/errors.py src/dext_recommend/__init__.py tests/test_recommend_errors.py
git commit -m "feat(rec): add structured error codes and RecommendationError"
```

---

### Task 4: ActiveBuildSnapshot (immutable)

**Files:**
- Create: `src/dext_recommend/readiness.py`
- Modify: `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_models.py` (snapshot portion)

**Interfaces:**
- Produces: `ActiveBuildSnapshot` frozen dataclass (12 fields from readiness §2). `ReadinessService` is a placeholder class with `check()`/`get_snapshot()` raising `NotImplementedError` (real logic in R2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_models.py  (snapshot portion — appended in this task)
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from dext_recommend import ActiveBuildSnapshot, ReadinessService


def _make_snapshot(**overrides):
    base = dict(
        build_id="b-1",
        catalog_schema_version=6,
        neo4j_active_build_id="b-1",
        qdrant_alias_target="dext_professors_current__phys-1",
        qdrant_payload_schema_version=2,
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dimension=1024,
        embedding_fingerprint="fp-xyz",
        taxonomy_version="tax-v1",
        ranking_profile_version="rank-v1",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ActiveBuildSnapshot(**base)


def test_active_build_snapshot_fields():
    snap = _make_snapshot()
    assert snap.build_id == "b-1"
    assert snap.embedding_dimension == 1024


def test_active_build_snapshot_is_frozen():
    snap = _make_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.build_id = "other"


def test_readiness_service_check_placeholder():
    svc = ReadinessService()
    with pytest.raises(NotImplementedError):
        svc.check()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'ActiveBuildSnapshot'`

- [ ] **Step 3: Implement ActiveBuildSnapshot + ReadinessService placeholder**

```python
# src/dext_recommend/readiness.py
"""ActiveBuildSnapshot + ReadinessService (overview §9, readiness spec).

The snapshot is the immutable per-request binding to an ACTIVE build. One
recommendation / detail / match / outreach / compare request uses exactly
one snapshot; refresh never merges old + new. Real build/refresh logic
lands in R2; this module fixes the data shape now so ports can take it as
an explicit parameter (foundations §5).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dext_recommend.errors import RecommendationError


@dataclass(frozen=True, slots=True)
class ActiveBuildSnapshot:
    build_id: str
    catalog_schema_version: int
    neo4j_active_build_id: str
    qdrant_alias_target: str
    qdrant_payload_schema_version: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_fingerprint: str
    taxonomy_version: str | None
    ranking_profile_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    snapshot: ActiveBuildSnapshot | None
    errors: list[RecommendationError]
    payload_coverage: dict[str, "CoverageStat"]


@dataclass(frozen=True, slots=True)
class CoverageStat:
    field: str
    covered: float          # 0..1 fraction
    sample_size: int
    passes: bool


class ReadinessService:
    """Placeholder; R2 implements real ACTIVE-build construction + checks."""

    def check(self) -> ReadinessReport:
        raise NotImplementedError("readiness implemented in R2")

    def get_snapshot(self) -> ActiveBuildSnapshot:
        raise NotImplementedError("readiness implemented in R2")


__all__ = ["ActiveBuildSnapshot", "CoverageStat", "ReadinessReport", "ReadinessService"]
```

Update `__init__.py`:

```python
# src/dext_recommend/__init__.py
from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)
from dext_recommend.readiness import (
    ActiveBuildSnapshot, CoverageStat, ReadinessReport, ReadinessService,
)

__all__ = [
    "ActiveBuildSnapshot",
    "CoverageStat",
    "ErrorSeverity",
    "ReadinessReport",
    "ReadinessService",
    "RecommendSettings",
    "RecommendationError",
    "RecommendationErrorCode",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/readiness.py src/dext_recommend/__init__.py tests/test_recommend_models.py
git commit -m "feat(rec): add immutable ActiveBuildSnapshot and ReadinessService placeholder"
```

---

### Task 5: Internal models (request/response/filters/conversation/query understanding)

**Files:**
- Create: `src/dext_recommend/models.py`
- Modify: `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_models.py` (append model tests)

**Interfaces:**
- Consumes: `StudentContext` from `dext_grounded` (re-exported, NOT redefined).
- Produces: `RecommendationFilters`, `ConversationContext`, `QueryUnderstanding`, `RecommendationWarning`, `QueryDiagnostics`, `RecommendedProfessor`, `RecommendRequest`, `RecommendResponse`. Field names strictly match overview §8/§10.

- [ ] **Step 1: Write the failing test (append to test_recommend_models.py)**

```python
# appended to tests/test_recommend_models.py
from dext_recommend import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendationFilters, RecommendResponse, RecommendedProfessor,
)
from dext_grounded import StudentContext


def test_recommendation_filters_defaults():
    f = RecommendationFilters()
    assert f.university_ids == []
    assert f.master_eligibility == "any"
    assert f.phd_eligibility == "any"
    assert f.topic_filter_mode == "soft"


def test_recommend_request_defaults():
    req = RecommendRequest(query_text="NLP 导师")
    assert req.limit == 10
    assert req.oversample == 200
    assert req.ranking_mode == "explainable_precision"
    assert req.review_policy == "exclude"
    assert req.include_contacts is False
    assert req.diagnostics_level == "summary"
    assert req.student_context is None
    assert req.conversation_context is None


def test_recommend_request_reexports_student_context():
    # StudentContext comes from dext_grounded, not redefined here
    req = RecommendRequest(
        query_text="x",
        student_context=StudentContext(school="X"),
    )
    assert req.student_context.school == "X"


def test_conversation_context_fields():
    ctx = ConversationContext(
        session_id="s1", turn_id="t1",
        intent="more_mentors", intent_source="explicit",
        prior_result_entity_ids=["e1", "e2"],
    )
    assert ctx.anchor_entity_id is None
    assert ctx.intent_confidence is None


def test_query_understanding_fields():
    qu = QueryUnderstanding(
        research_interests=["NLP"],
        preferred_universities=["A"],
        preferred_cities=["北京"],
        preferred_org_units=[],
        degree_goal="phd",
        mentor_eligibility_requirement="phd_confirmed",
        missing_information=["gpa"],
        needs_clarification=False,
        confidence=0.8,
    )
    assert qu.research_interests == ["NLP"]
    assert qu.confidence == 0.8


def test_recommended_professor_minimum():
    p = RecommendedProfessor(
        entity_id="e1", display_name="Prof", university="U",
        org_units=[], title="Professor", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="unknown",
        role_status="included", profile_url="http://x",
        research_summary="RAG", match_level="strong",
        short_reasons=["works on RAG"], score=0.9, score_components={},
        matched_topics=[], matched_statements=[], matched_publications=[],
        evidence_refs=[], risk_flags=[], available_actions=["detail"],
    )
    assert p.entity_id == "e1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'RecommendationFilters'`

- [ ] **Step 3: Implement models**

```python
# src/dext_recommend/models.py
"""Internal request/response models (overview §8, §10).

Field names match the overview spec strictly. StudentContext and SourceRef
are imported from the shared dext_grounded contract — never redefined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded import SourceRef, StudentContext


# ---- Filters & conversation (overview §8) ----

@dataclass(frozen=True, slots=True)
class RecommendationFilters:
    university_ids: list[str] = field(default_factory=list)
    city_names: list[str] = field(default_factory=list)
    org_unit_ids: list[str] = field(default_factory=list)
    title_families: list[str] = field(default_factory=list)
    master_eligibility: str = "any"          # any|confirmed
    phd_eligibility: str = "any"              # any|confirmed
    topic_ids: list[str] = field(default_factory=list)
    topic_filter_mode: str = "soft"           # soft|hard


@dataclass(frozen=True, slots=True)
class ConversationContext:
    session_id: str | None = None
    turn_id: str | None = None
    main_session_id: str | None = None        # fork relationship
    source_turn_id: str | None = None
    anchor_entity_id: str | None = None
    intent: str | None = None
    # new_search|more_mentors|same_field|refine_direction|detail_followup
    intent_source: str | None = None          # explicit|implicit
    intent_confidence: float | None = None
    prior_result_entity_ids: list[str] = field(default_factory=list)


# ---- Query understanding (overview §10) ----

@dataclass(frozen=True, slots=True)
class QueryUnderstanding:
    research_interests: list[str]
    preferred_universities: list[str]
    preferred_cities: list[str]
    preferred_org_units: list[str]
    degree_goal: str | None
    mentor_eligibility_requirement: str | None
    missing_information: list[str]
    needs_clarification: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class QueryDiagnostics:
    query_length: int
    language_summary: str | None
    filter_summary: str | None
    recall_count: int = 0
    post_filter_count: int = 0
    returned_count: int = 0


@dataclass(frozen=True, slots=True)
class RecommendationWarning:
    code: str
    message: str
    severity: str = "warning"


# ---- Result & response (overview §8, §5) ----

@dataclass(frozen=True, slots=True)
class RecommendedProfessor:
    entity_id: str
    display_name: str
    university: str
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_summary: str | None
    match_level: str                       # excellent|strong|possible|weak
    short_reasons: list[str]
    score: float
    score_components: dict[str, float]
    matched_topics: list[str]
    matched_statements: list[str]
    matched_publications: list[str]
    evidence_refs: list[SourceRef]
    risk_flags: list[str]
    available_actions: list[str]            # detail|match|email|compare|favorite|follow_up


@dataclass(frozen=True, slots=True)
class RecommendRequest:
    query_text: str
    student_context: StudentContext | None = None
    filters: RecommendationFilters = field(default_factory=RecommendationFilters)
    conversation_context: ConversationContext | None = None
    limit: int = 10
    oversample: int = 200
    ranking_mode: str = "explainable_precision"
    review_policy: str = "exclude"          # exclude|include_downranked
    include_contacts: bool = False
    diagnostics_level: str = "summary"      # none|summary|debug


@dataclass(frozen=True, slots=True)
class RecommendResponse:
    build_id: str
    ranking_profile_version: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    query_understanding: QueryUnderstanding
    query: QueryDiagnostics
    results: list[RecommendedProfessor]
    suggested_followups: list[str]
    warnings: list[RecommendationWarning]


__all__ = [
    "ConversationContext",
    "QueryDiagnostics",
    "QueryUnderstanding",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
]
```

Update `__init__.py`:

```python
# src/dext_recommend/__init__.py
from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)
from dext_grounded import SourceRef, StudentContext  # re-export, not redefine (spec §3)
from dext_recommend.models import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendResponse, RecommendationFilters, RecommendationWarning,
    RecommendedProfessor,
)
from dext_recommend.readiness import (
    ActiveBuildSnapshot, CoverageStat, ReadinessReport, ReadinessService,
)

__all__ = [
    "ActiveBuildSnapshot",
    "ConversationContext",
    "CoverageStat",
    "ErrorSeverity",
    "QueryDiagnostics",
    "QueryUnderstanding",
    "ReadinessReport",
    "ReadinessService",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendSettings",
    "RecommendationError",
    "RecommendationErrorCode",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
    "SourceRef",        # re-exported from dext_grounded (spec §3/§9)
    "StudentContext",   # re-exported from dext_grounded (spec §3/§9)
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: PASS (3 prior + 6 new = 9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/models.py src/dext_recommend/__init__.py tests/test_recommend_models.py
git commit -m "feat(rec): add internal request/response models re-exporting grounded StudentContext"
```

---

### Task 6: Ports — build_snapshot, embedding, vector_search, professor_facts, generation

**Files:**
- Create: `src/dext_recommend/ports/build_snapshot.py`, `embedding.py`, `vector_search.py`, `professor_facts.py`, `generation.py`
- Modify: `src/dext_recommend/ports/__init__.py`, `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_ports_snapshot_contract.py`

**Interfaces:**
- Consumes: `ActiveBuildSnapshot` (Task 4), `RecommendationFilters` (Task 5), `dext_grounded.LLMGenerationPort` (R0).
- Produces: 5 `Protocol`s with explicit-snapshot signatures: `BuildSnapshotPort.get_snapshot()/refresh()`, `QueryEmbeddingPort.embed(snapshot, query_text) -> EmbeddingResult`, `VectorSearchPort.hybrid_recall(snapshot, query_vector, filters, oversample, profile_version) -> list[VectorHit]` + `alias_readback/count_readback`, `ProfessorFactPort.get_detail(snapshot, entity_id, include_contacts, viewer_permissions) -> ProfessorDetail` + `hydrate(snapshot, entity_ids) -> dict`, `generation.py` re-exports `LLMGenerationPort` from `dext_grounded`. Helper dataclasses: `EmbeddingResult`, `VectorHit`, `AliasReadback`, `ProfessorFact`, `ProfessorDetail`, `ViewerPermissions`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_ports_snapshot_contract.py
"""Every port data method must take an ActiveBuildSnapshot as an explicit param."""
from __future__ import annotations

import inspect

from dext_recommend import ActiveBuildSnapshot
from dext_recommend.ports import (
    BuildSnapshotPort, EmbeddingResult, ProfessorFactPort, ProfessorDetail,
    QueryEmbeddingPort, VectorHit, VectorSearchPort,
)


def _sig_params(protocol_method):
    return list(inspect.signature(protocol_method).parameters)


def test_vector_search_hybrid_recall_takes_snapshot():
    params = _sig_params(VectorSearchPort.hybrid_recall)
    assert "snapshot" in params
    assert "query_vector" in params
    assert "filters" in params
    assert "oversample" in params
    assert "profile_version" in params


def test_vector_search_alias_readback_takes_snapshot():
    assert "snapshot" in _sig_params(VectorSearchPort.alias_readback)


def test_vector_search_count_readback_takes_snapshot():
    assert "snapshot" in _sig_params(VectorSearchPort.count_readback)


def test_professor_fact_get_detail_takes_snapshot():
    params = _sig_params(ProfessorFactPort.get_detail)
    assert params[1] == "snapshot"
    assert "entity_id" in params
    assert "include_contacts" in params
    assert "viewer_permissions" in params


def test_professor_fact_hydrate_takes_snapshot():
    params = _sig_params(ProfessorFactPort.hydrate)
    assert params[1] == "snapshot"
    assert "entity_ids" in params


def test_query_embedding_embed_takes_snapshot():
    params = _sig_params(QueryEmbeddingPort.embed)
    assert params[1] == "snapshot"
    assert "query_text" in params


def test_embedding_result_carries_fingerprint():
    r = EmbeddingResult(vector=[0.1, 0.2], embedding_fingerprint="fp-x")
    assert r.embedding_fingerprint == "fp-x"


def test_build_snapshot_port_has_get_and_refresh():
    assert hasattr(BuildSnapshotPort, "get_snapshot")
    assert hasattr(BuildSnapshotPort, "refresh")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_ports_snapshot_contract.py -v`
Expected: FAIL with `ImportError: cannot import name 'BuildSnapshotPort'`

- [ ] **Step 3: Implement ports**

```python
# src/dext_recommend/ports/build_snapshot.py
"""BuildSnapshotPort — read ACTIVE build snapshot (foundations §5)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot


@runtime_checkable
class BuildSnapshotPort(Protocol):
    def get_snapshot(self) -> ActiveBuildSnapshot: ...

    def refresh(self) -> ActiveBuildSnapshot | None:
        """Refresh failure returns None; never pollutes the validated snapshot."""
        ...


__all__ = ["BuildSnapshotPort"]
```

```python
# src/dext_recommend/ports/embedding.py
"""QueryEmbeddingPort — encode query with ACTIVE-build embedding settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: list[float]
    embedding_fingerprint: str
    sparse_vector: dict | None = None   # {indices: [...], values: [...]}


@runtime_checkable
class QueryEmbeddingPort(Protocol):
    def embed(self, snapshot: ActiveBuildSnapshot, query_text: str) -> EmbeddingResult:
        ...


__all__ = ["EmbeddingResult", "QueryEmbeddingPort"]
```

```python
# src/dext_recommend/ports/vector_search.py
"""VectorSearchPort — hybrid recall + alias/count readback (foundations §5).

All methods take the request-pinned ActiveBuildSnapshot explicitly so a
mid-request alias/pointer switch cannot mix new+old build versions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from dext_recommend.models import RecommendationFilters
from dext_recommend.readiness import ActiveBuildSnapshot


@dataclass(frozen=True, slots=True)
class VectorHit:
    entity_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AliasReadback:
    alias: str
    target_collection: str
    build_id: str | None
    payload_schema_version: int | None


@runtime_checkable
class VectorSearchPort(Protocol):
    def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters,
        oversample: int,
        profile_version: str,
    ) -> list[VectorHit]: ...

    def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback: ...

    def count_readback(
        self, snapshot: ActiveBuildSnapshot, filter: dict | None = None,
    ) -> int: ...


__all__ = ["AliasReadback", "VectorHit", "VectorSearchPort"]
```

```python
# src/dext_recommend/ports/professor_facts.py
"""ProfessorFactPort — assemble ProfessorDetail / hydrate facts (R4 fills)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dext_grounded import SourceRef

from dext_recommend.readiness import ActiveBuildSnapshot


@dataclass(frozen=True, slots=True)
class ViewerPermissions:
    include_contacts: bool = False
    can_view_review: bool = False
    diagnostics: bool = False


@dataclass(frozen=True, slots=True)
class ProfessorFact:
    entity_id: str
    display_name: str
    university: str
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    profile_hash: str | None
    research_summary: str | None


@dataclass(frozen=True, slots=True)
class ProfessorDetail:
    build_id: str
    profile_hash: str | None
    entity_id: str
    display_name: str
    university: str
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_statements: list[str]
    approved_topics: list[str]
    selected_publication_mentions: list[str]
    bio_snippets: list[str]
    source_urls: list[str]
    provenance_refs: list[SourceRef]
    quality_findings: list[str]
    risk_flags: list[str]
    contacts: dict[str, str] = field(default_factory=dict)   # gated by viewer perms


@runtime_checkable
class ProfessorFactPort(Protocol):
    def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail: ...

    def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]: ...


__all__ = ["ProfessorDetail", "ProfessorFact", "ProfessorFactPort", "ViewerPermissions"]
```

```python
# src/dext_recommend/ports/generation.py
"""Re-export the shared LLMGenerationPort (foundations §5, grounded §4)."""
from __future__ import annotations

from dext_grounded import FakeLLMGenerationPort, LLMGenerationPort

__all__ = ["FakeLLMGenerationPort", "LLMGenerationPort"]
```

Update `ports/__init__.py`:

```python
# src/dext_recommend/ports/__init__.py
from dext_recommend.ports.build_snapshot import BuildSnapshotPort
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.generation import FakeLLMGenerationPort, LLMGenerationPort
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactPort, ViewerPermissions,
)
from dext_recommend.ports.vector_search import AliasReadback, VectorHit, VectorSearchPort

__all__ = [
    "AliasReadback",
    "BuildSnapshotPort",
    "EmbeddingResult",
    "FakeLLMGenerationPort",
    "LLMGenerationPort",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactPort",
    "QueryEmbeddingPort",
    "VectorHit",
    "VectorSearchPort",
    "ViewerPermissions",
]
```

Update top-level `__init__.py` (add ports re-exports — append to the existing import block):

```python
# src/dext_recommend/__init__.py — add at top after existing imports
from dext_recommend.ports import (
    AliasReadback, BuildSnapshotPort, EmbeddingResult, FakeLLMGenerationPort,
    LLMGenerationPort, ProfessorDetail, ProfessorFact, ProfessorFactPort,
    QueryEmbeddingPort, VectorHit, VectorSearchPort, ViewerPermissions,
)
```

and append these names to `__all__`:

```python
    "AliasReadback",
    "BuildSnapshotPort",
    "EmbeddingResult",
    "FakeLLMGenerationPort",
    "LLMGenerationPort",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactPort",
    "QueryEmbeddingPort",
    "VectorHit",
    "VectorSearchPort",
    "ViewerPermissions",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_ports_snapshot_contract.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/ports src/dext_recommend/__init__.py tests/test_recommend_ports_snapshot_contract.py
git commit -m "feat(rec): add five ports with explicit-snapshot contracts"
```

---

### Task 7: Fake ports (4)

**Files:**
- Create: `src/dext_recommend/ports/_fakes.py`
- Modify: `src/dext_recommend/ports/__init__.py`, `src/dext_recommend/__init__.py`
- Test: `tests/test_recommend_fake_ports.py`

**Interfaces:**
- Consumes: all ports (Task 6).
- Produces: `FakeBuildSnapshotPort` (inject preset snapshot, simulate no-ACTIVE/inconsistent), `FakeQueryEmbeddingPort` (fixed vector + matching fingerprint), `FakeVectorSearchPort` (inject hits + readback), `FakeProfessorFactPort` (inject detail bundles). All satisfy their `Protocol`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recommend_fake_ports.py
from __future__ import annotations

from datetime import datetime, timezone

from dext_recommend import (
    ActiveBuildSnapshot, BuildSnapshotPort, FakeBuildSnapshotPort,
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeVectorSearchPort,
    ProfessorDetail, ProfessorFactPort, QueryEmbeddingPort, VectorSearchPort,
)


def _snap():
    return ActiveBuildSnapshot(
        build_id="b-1", catalog_schema_version=6, neo4j_active_build_id="b-1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="sf", embedding_model="bge-m3", embedding_dimension=1024,
        embedding_fingerprint="fp-x", taxonomy_version="t1",
        ranking_profile_version="r1", created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_fake_build_snapshot_port_satisfies_protocol():
    port = FakeBuildSnapshotPort(_snap())
    assert isinstance(port, BuildSnapshotPort)
    assert port.get_snapshot().build_id == "b-1"


def test_fake_build_snapshot_port_no_active():
    port = FakeBuildSnapshotPort(None)
    assert port.get_snapshot() is None


def test_fake_query_embedding_port_returns_fixed_vector_and_fingerprint():
    port = FakeQueryEmbeddingPort(vector=[0.1, 0.2], fingerprint="fp-x")
    assert isinstance(port, QueryEmbeddingPort)
    result = port.embed(_snap(), "NLP")
    assert result.vector == [0.1, 0.2]
    assert result.embedding_fingerprint == "fp-x"


def test_fake_vector_search_port_returns_preset_hits():
    from dext_recommend import VectorHit
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    port = FakeVectorSearchPort(hits=hits)
    assert isinstance(port, VectorSearchPort)
    out = port.hybrid_recall(_snap(), [0.1], filters=None, oversample=200, profile_version="r1")
    assert out == hits


def test_fake_professor_fact_port_returns_preset_detail():
    detail = ProfessorDetail(
        build_id="b-1", profile_hash=None, entity_id="e1", display_name="P",
        university="U", org_units=[], title="Prof", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="unknown",
        role_status="included", profile_url=None, research_statements=[],
        approved_topics=[], selected_publication_mentions=[], bio_snippets=[],
        source_urls=[], provenance_refs=[], quality_findings=[], risk_flags=[],
    )
    port = FakeProfessorFactPort(details={"e1": detail})
    assert isinstance(port, ProfessorFactPort)
    from dext_recommend import ViewerPermissions
    got = port.get_detail(_snap(), "e1", False, ViewerPermissions())
    assert got is detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_fake_ports.py -v`
Expected: FAIL with `ImportError: cannot import name 'FakeBuildSnapshotPort'`

- [ ] **Step 3: Implement fakes**

```python
# src/dext_recommend/ports/_fakes.py
"""Fake ports for unit tests (foundations §6). Test-only; never in composition root."""
from __future__ import annotations

from dext_recommend.ports.build_snapshot import BuildSnapshotPort
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactPort, ViewerPermissions,
)
from dext_recommend.ports.vector_search import (
    AliasReadback, VectorHit, VectorSearchPort,
)
from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend.models import RecommendationFilters


class FakeBuildSnapshotPort:
    """Inject a preset snapshot (None simulates no ACTIVE build)."""

    def __init__(self, snapshot: ActiveBuildSnapshot | None) -> None:
        self._snapshot = snapshot

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        return self._snapshot

    def refresh(self) -> ActiveBuildSnapshot | None:
        return self._snapshot


class FakeQueryEmbeddingPort:
    def __init__(self, vector: list[float], fingerprint: str) -> None:
        self._vector = vector
        self._fingerprint = fingerprint

    def embed(self, snapshot: ActiveBuildSnapshot, query_text: str) -> EmbeddingResult:
        return EmbeddingResult(vector=list(self._vector), embedding_fingerprint=self._fingerprint)


class FakeVectorSearchPort:
    def __init__(
        self,
        hits: list[VectorHit] | None = None,
        alias: AliasReadback | None = None,
        count: int = 0,
    ) -> None:
        self._hits = hits or []
        self._alias = alias
        self._count = count

    def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters | None,
        oversample: int,
        profile_version: str,
    ) -> list[VectorHit]:
        return list(self._hits)

    def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback:
        return self._alias or AliasReadback(
            alias="dext_professors_current", target_collection="phys-1",
            build_id=snapshot.build_id, payload_schema_version=2,
        )

    def count_readback(self, snapshot: ActiveBuildSnapshot, filter: dict | None = None) -> int:
        return self._count


class FakeProfessorFactPort:
    def __init__(
        self,
        details: dict[str, ProfessorDetail] | None = None,
        facts: dict[str, ProfessorFact] | None = None,
    ) -> None:
        self._details = details or {}
        self._facts = facts or {}

    def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        return self._details[entity_id]

    def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        return {eid: self._facts[eid] for eid in entity_ids if eid in self._facts}


__all__ = [
    "FakeBuildSnapshotPort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeVectorSearchPort",
]
```

Update `ports/__init__.py` (add fakes):

```python
# append to src/dext_recommend/ports/__init__.py
from dext_recommend.ports._fakes import (
    FakeBuildSnapshotPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort,
)
```

and append `"FakeBuildSnapshotPort"`, `"FakeProfessorFactPort"`, `"FakeQueryEmbeddingPort"`, `"FakeVectorSearchPort"` to its `__all__`.

Update top-level `__init__.py` (add fakes import + `__all__` entries):

```python
# add to the ports import block in src/dext_recommend/__init__.py
from dext_recommend.ports import (
    AliasReadback, BuildSnapshotPort, EmbeddingResult, FakeBuildSnapshotPort,
    FakeLLMGenerationPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort, LLMGenerationPort, ProfessorDetail, ProfessorFact,
    ProfessorFactPort, QueryEmbeddingPort, VectorHit, VectorSearchPort,
    ViewerPermissions,
)
```

and append `"FakeBuildSnapshotPort"`, `"FakeProfessorFactPort"`, `"FakeQueryEmbeddingPort"`, `"FakeVectorSearchPort"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_fake_ports.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/ports/_fakes.py src/dext_recommend/ports/__init__.py src/dext_recommend/__init__.py tests/test_recommend_fake_ports.py
git commit -m "feat(rec): add four fake ports for unit testing"
```

---

### Task 8: core/service.py placeholder orchestrator

**Files:**
- Create: `src/dext_recommend/core/service.py`
- Modify: `src/dext_recommend/core/__init__.py`
- Test: `tests/test_recommend_models.py` (append placeholder test)

**Interfaces:**
- Consumes: `RecommendRequest`, `RecommendResponse` (Task 5), `BuildSnapshotPort`, `QueryEmbeddingPort`, `VectorSearchPort`, `ProfessorFactPort` (Tasks 6–7).
- Produces: `RecommendationCore` class constructed from the four ports; `recommend(request) -> RecommendResponse` raises `NotImplementedError` (real pipeline in R3). Constructor accepts ports via a frozen `RecommendDeps` dataclass.

- [ ] **Step 1: Write the failing test (append to test_recommend_models.py)**

```python
# appended to tests/test_recommend_models.py
from dext_recommend import RecommendationCore, RecommendDeps
from dext_recommend import (
    FakeBuildSnapshotPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort,
)


def test_recommendation_core_constructs_from_fake_ports():
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    assert core is not None


def test_recommendation_core_recommend_placeholder():
    import pytest
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    with pytest.raises(NotImplementedError):
        core.recommend(RecommendRequest(query_text="x"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'RecommendationCore'`

- [ ] **Step 3: Implement placeholder orchestrator**

```python
# src/dext_recommend/core/service.py
"""RecommendRequest -> RecommendResponse orchestrator (placeholder; pipeline in R3).

The core holds its four ports via an immutable RecommendDeps. The real pipeline
(query understanding -> embedding -> hybrid recall -> hydration -> rerank ->
explanation -> cards) lands in R3; for now only the wiring shape is fixed so
import boundaries and the fake-port contract can be exercised.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_recommend.models import RecommendRequest, RecommendResponse
from dext_recommend.ports import (
    BuildSnapshotPort, ProfessorFactPort, QueryEmbeddingPort, VectorSearchPort,
)


@dataclass(frozen=True, slots=True)
class RecommendDeps:
    snapshot_port: BuildSnapshotPort
    embedding_port: QueryEmbeddingPort
    vector_port: VectorSearchPort
    facts_port: ProfessorFactPort


class RecommendationCore:
    def __init__(self, deps: RecommendDeps) -> None:
        self._deps = deps

    @property
    def deps(self) -> RecommendDeps:
        return self._deps

    def recommend(self, request: RecommendRequest) -> RecommendResponse:
        raise NotImplementedError("recommend pipeline implemented in R3")


__all__ = ["RecommendDeps", "RecommendationCore"]
```

Update `core/__init__.py`:

```python
# src/dext_recommend/core/__init__.py
from dext_recommend.core.service import RecommendDeps, RecommendationCore

__all__ = ["RecommendDeps", "RecommendationCore"]
```

Update top-level `__init__.py` (add core re-exports — append to import block + `__all__`):

```python
# add to src/dext_recommend/__init__.py import block
from dext_recommend.core import RecommendDeps, RecommendationCore
```

append `"RecommendDeps"`, `"RecommendationCore"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recommend_models.py -v`
Expected: PASS (9 prior + 2 new = 11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/core src/dext_recommend/__init__.py tests/test_recommend_models.py
git commit -m "feat(rec): add RecommendationCore placeholder with fake-port wiring"
```

---

### Task 9: Foundations acceptance — full suite green + spec §9

**Files:**
- Test: `tests/test_recommend_foundations_acceptance.py`

**Interfaces:**
- Consumes: all of Tasks 1–8.

- [ ] **Step 1: Write the acceptance test (foundations §9)**

```python
# tests/test_recommend_foundations_acceptance.py
"""Foundations §9 acceptance: package, models, errors, ports, fakes, boundary."""
from __future__ import annotations

import dataclasses

import pytest

import dext_recommend as rec
from dext_recommend import (
    ActiveBuildSnapshot, BuildSnapshotPort, ConversationContext, ErrorSeverity,
    FakeBuildSnapshotPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort, ProfessorFactPort, QueryEmbeddingPort,
    QueryUnderstanding, RecommendDeps, RecommendationCore,
    RecommendationErrorCode, RecommendationFilters, RecommendRequest,
    VectorSearchPort, ViewerPermissions,
)
import dext_grounded as grounded


def _snap():
    from datetime import datetime, timezone
    return ActiveBuildSnapshot(
        build_id="b-1", catalog_schema_version=6, neo4j_active_build_id="b-1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="sf", embedding_model="bge-m3", embedding_dimension=1024,
        embedding_fingerprint="fp-x", taxonomy_version="t1",
        ranking_profile_version="r1", created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_models_reexport_grounded_student_context():
    # Spec §3/§9: identity equivalence, NOT a behavioral substitute.
    # The implemented test must NOT be weakened to "RecommendRequest accepts a
    # grounded StudentContext" — that only proves behavioral compatibility, not
    # the re-export contract.
    assert rec.StudentContext is grounded.StudentContext
    assert rec.SourceRef is grounded.SourceRef
    req = RecommendRequest(query_text="x", student_context=grounded.StudentContext(school="S"))
    assert req.student_context.school == "S"


def test_config_model_dump_redacts_secrets_by_default():
    # Spec §7: model_dump itself must not leak keys (SecretStr), not just safe_snapshot().
    from dext_recommend import RecommendSettings
    s = RecommendSettings(
        embedding_api_key="secret-embed",
        llm_api_key="secret-llm",
        neo4j_password="secret-pw",
    )
    dumped = s.model_dump(mode="json")
    dumped_json = s.model_dump_json()
    for secret in ("secret-embed", "secret-llm", "secret-pw"):
        assert secret not in str(dumped)
        assert secret not in dumped_json
    assert dumped["qdrant_alias"] == "dext_professors_current"


def test_response_collections_are_deeply_immutable():
    # Spec §3/§9: frozen dataclass + mutable list is not "immutable".
    rp = RecommendedProfessor(
        entity_id="e1", display_name="P", university="U", org_units=[],
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="unknown", role_status="included", profile_url=None,
        research_summary="R", match_level="strong", short_reasons=["x"], score=0.9,
        score_components={}, matched_topics=[], matched_statements=[],
        matched_publications=[], evidence_refs=[], risk_flags=[],
        available_actions=["detail"],
    )
    with pytest.raises((AttributeError, TypeError)):
        rp.matched_topics.append("y")
    with pytest.raises((AttributeError, TypeError)):
        RecommendationFilters().university_ids.append("U")
    with pytest.raises((AttributeError, TypeError)):
        ConversationContext(prior_result_entity_ids=["e1"]).prior_result_entity_ids.append("e2")


def test_snapshot_is_immutable():
    snap = _snap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.build_id = "other"


def test_all_four_fakes_satisfy_protocols():
    snap = _snap()
    assert isinstance(FakeBuildSnapshotPort(snap), BuildSnapshotPort)
    assert isinstance(
        FakeQueryEmbeddingPort([0.1], "fp-x"), QueryEmbeddingPort,
    )
    assert isinstance(FakeVectorSearchPort(), VectorSearchPort)
    assert isinstance(FakeProfessorFactPort(), ProfessorFactPort)


def test_core_wired_with_fakes_does_not_touch_real_services():
    snap = _snap()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    # placeholder recommend raises NotImplementedError, but construction is clean
    with pytest.raises(NotImplementedError):
        core.recommend(RecommendRequest(query_text="x"))


def test_error_codes_complete():
    codes = {c.value for c in RecommendationErrorCode}
    assert "active_build_unavailable" in codes
    assert "no_candidates_after_filters" in codes
    assert "unauthorized_contact" in codes
```

- [ ] **Step 2: Run the whole recommend test suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (single-module green — full-project `pytest -q` is NOT required; known timeout)

- [ ] **Step 4: Commit**

```bash
git add tests/test_recommend_foundations_acceptance.py
git commit -m "test(rec): add foundations §9 acceptance test"
```

> **Reproducibility gate (must):** the R0/R1 plan files (`docs/superpowers/plans/2026-07-01-dext-recommend-0{0,1}-*.md`) and the grounded rules module (`src/dext_grounded/rules.py`, `src/dext_grounded/rules/grounded_v1.yaml`) are load-bearing for `import dext_recommend` (which imports `dext_grounded`, which imports the rules at module load). They MUST be `git add`-ed; committing only currently-tracked files breaks the import.

---

## Self-Review

**1. Spec coverage** (foundations §1–§9):
- §2 package structure (api/ deferred to R7) → Task 1 ✓
- §3 internal models (re-export grounded StudentContext/SourceRef — now **identity-asserted at top level** + **deeply immutable**) → Task 5 ✓
- §4 error codes (8 codes) → Task 3 ✓
- §5 ports (5 protocols, explicit-snapshot param) → Task 6 ✓
- §6 fake ports (4) → Task 7 ✓
- §7 config (env keys, secrets **`SecretStr`** so default `model_dump` redacts, not just `safe_snapshot`) → Task 2 ✓
- §8 import boundary (no dext/dext_graph/dext_monitor/dext_competition; may import dext_grounded) → Tasks 1, 9 ✓
- §9 acceptance (structure, models, errors, ports, fakes, boundary test, **re-export identity**, **config-dump redaction**, **deep immutability**, **single-module green**) → Task 9 ✓

**2. Placeholder scan:** No TBD/TODO in code steps. `ReadinessService.check`/`get_snapshot` and `RecommendationCore.recommend` deliberately raise `NotImplementedError` — these are phase-gated placeholders explicitly handed off to R2/R3 (called out in task descriptions), not unfilled gaps.

**3. Type consistency:** `ActiveBuildSnapshot` (Task 4) — used as the explicit param in every port (Task 6) and accepted by fakes (Task 7). `RecommendationFilters` (Task 5) — passed to `VectorSearchPort.hybrid_recall` (Task 6). `ProfessorDetail`/`ProfessorFact`/`ViewerPermissions` (Task 6) — used by `ProfessorFactPort` and `FakeProfessorFactPort` (Task 7). `EmbeddingResult.embedding_fingerprint` (Task 6) — matches the snapshot fingerprint comparison referenced in foundations §5. `RecommendDeps` (Task 8) — holds exactly the four ports defined in Task 6. `StudentContext` is imported from `dext_grounded` in `models.py` (Task 5), never redefined — matches the spec's "re-export rather than redefine" rule.

**Execution note:** This plan is fully unblocked — all tests use fake ports, no real catalog/Qdrant/Neo4j/LLM. It lands the R1 prerequisite for R2 (readiness), R3 (core), R4 (facts), R5 (conversation), R6 (generation).
