# 阶段 1：dext_recommend foundations

> 状态：设计稿
>
> 前置依赖：[共享 grounded-generation 契约](2026-06-30-dext-grounded-generation-design.md)已定义；[recommend overview](2026-06-30-dext-recommendation-system-design.md)的模块边界与 §6.3 包结构已确定
>
> 后续阶段：[Readiness](2026-06-30-dext-recommendation-02-readiness-design.md)

## 1. 目标

建立 `dext_recommend` 包骨架：内部模型、错误码、配置、ports、adapters 接口与 fake ports。本阶段不实现真实数据读取，只把推荐核心与外部依赖之间的边界用稳定接口固化，使后续阶段能用 fake ports 单测核心逻辑，并保证 import 边界不被破坏。

## 2. 包结构

按 overview §6.3 落地目录，本阶段至少创建：

```text
src/dext_recommend/
  __init__.py
  config.py
  models.py
  errors.py
  readiness.py            # 仅占位与类型签名，阶段 2 填充
  ports/
    __init__.py
    build_snapshot.py
    vector_search.py
    professor_facts.py
    embedding.py
    generation.py          # re-export 共享 LLMGenerationPort
  adapters/
    __init__.py            # 仅声明 adapter 名，阶段 2 起逐个填充
  core/
    __init__.py
    service.py             # 仅 RecommendRequest -> RecommendResponse 占位
  facts/__init__.py
  generation/__init__.py
  eval/__init__.py
  tests/
    test_import_boundary.py
    test_models.py
    test_errors.py
```

`api/` 目录阶段 7 创建；本阶段不建 HTTP 层。

## 3. 内部模型

固化 overview §8 的数据结构为稳定 dataclass 或 pydantic model，本阶段只定义、不实现业务：

- `RecommendRequest`、`RecommendResponse`、`RecommendedProfessor`
- `StudentContext`（与共享契约对齐，re-export 而非重复定义）
- `ConversationContext`、`RecommendationFilters`
- `QueryUnderstanding`、`RecommendationWarning`

字段名与 overview §8、§10 严格一致；`StudentContext`、`SourceRef` 从共享契约 import，不重复定义。

## 4. 错误码

固化 overview §17 的结构化错误模型：

```text
RecommendationError
  code: str
  severity: "warning|error"
  message: str
  build_id: str | null
  retryable: bool
  operator_action: str | null
  user_action: str | null
```

初始错误码枚举（本阶段注册，后续阶段填充触发逻辑）：

- `active_build_unavailable`
- `active_build_inconsistent`
- `embedding_fingerprint_mismatch`
- `no_candidates_after_filters`
- `payload_prefilter_degraded`
- `insufficient_facts`
- `unauthorized_contact`
- `generation_unavailable`（re-export 共享）

## 5. Ports

每个 port 是 `dext_recommend` 自己定义的抽象协议，方法签名稳定但本阶段无实现：

```text
BuildSnapshotPort
  get_snapshot() -> ActiveBuildSnapshot
  refresh() -> ActiveBuildSnapshot | null

VectorSearchPort
  hybrid_recall(query_vector, filters, oversample, profile_version) -> list[VectorHit]
  alias_readback() -> AliasReadback
  count_readback(filter) -> int

ProfessorFactPort
  get_detail(entity_id, include_contacts, viewer_permissions) -> ProfessorDetail
  hydrate(entity_ids) -> dict[entity_id, ProfessorFact]

QueryEmbeddingPort
  embed(query_text) -> EmbeddingResult  # 含 fingerprint 校验

LLMGenerationPort
  # re-export 共享契约，不在本模块重复定义
```

`ProfessorDetail` 在本阶段定义为只读容器（阶段 4 填充组装逻辑）；`EmbeddingResult` 必须携带与 `ActiveBuildSnapshot.embedding_fingerprint` 比对所需的字段。

## 6. Fake ports

每个 port 提供一个 fake 实现，供后续阶段单测使用，不依赖真实外部服务：

- `FakeBuildSnapshotPort`：可注入预设 snapshot，可模拟缺 ACTIVE / 三端不一致。
- `FakeVectorSearchPort`：可注入预设 hits 与 readback 结果。
- `FakeProfessorFactPort`：可注入预设 detail bundle。
- `FakeQueryEmbeddingPort`：返回固定向量与一致 fingerprint。

fake ports 只用于测试，不进入运行时 composition root。

## 7. 配置

`config.py` 从环境读取，所有超时、limit、ranking profile 路径配置化：

```text
DEXT_RECOMMEND_BUILD_MANIFEST_PATH
DEXT_RECOMMEND_CATALOG_PATH
DEXT_RECOMMEND_QDRANT_URL
DEXT_RECOMMEND_QDRANT_ALIAS=dext_professors_current
DEXT_RECOMMEND_NEO4J_URL
DEXT_RECOMMEND_EMBEDDING_PROVIDER
DEXT_RECOMMEND_EMBEDDING_MODEL
DEXT_RECOMMEND_RANKING_PROFILE_PATH
DEXT_RECOMMEND_TOTAL_TIMEOUT=30
DEXT_RECOMMEND_OVERSAMPLE_DEFAULT=200
DEXT_RECOMMEND_OVERSAMPLE_MAX=1000
```

API key 只从环境读取，不进入配置对象的可序列化表示、日志或 manifest。

## 8. import 边界

`dext_recommend` 与 `dext`、`dext_graph`、`dext_monitor` 平级，**不 import** 这三者任何内部模块、ORM model、service class 或 CLI command。`dext_recommend` 与 `dext_competition` 互不 import，但都可 import 共享 grounded-generation 契约。

## 9. 验收标准

- 包结构按 §2 创建，`__init__.py` re-export 公开接口，模块形成无环 import DAG。
- §3 内部模型字段与 overview §8/§10 严格一致；`StudentContext`/`SourceRef` 从共享契约 import。
- §4 错误码枚举注册完成，结构化字段稳定。
- §5 五个 port 接口签名稳定，可被 fake 实现。
- §6 四个 fake port（除 LLMGeneration 共享）可支撑后续阶段核心逻辑单测。
- `test_import_boundary.py` 通过静态检查或 import 探针确认：`dext_recommend` 不 import `dext.*`、`dext_graph.*`、`dext_monitor.*`，且不 import `dext_competition.*`。
- 配置 key 命名稳定，API key 不出现在任何可序列化结构中。
