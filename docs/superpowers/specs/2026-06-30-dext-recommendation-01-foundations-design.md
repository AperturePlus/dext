# 阶段 1：dext_recommend foundations

> 状态：设计稿
>
> 前置依赖：[共享 grounded-generation 契约](2026-06-30-dext-grounded-generation-design.md)已定义；[recommend overview](2026-06-30-dext-recommendation-system-design.md)的模块边界与 §6.3 包结构已确定
>
> 内容安全增量：[防御性内容安全与导师保护](2026-07-02-dext-defensive-content-safety-design.md) 已纳入推荐系统共享错误码和生成边界
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
    active_snapshot.py       # 已验证 snapshot 的下游 provider
    release_readback.py      # R2 使用的 catalog/vector/graph 原始 readback
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

**re-export（必须）**：`StudentContext` 与 `SourceRef` 必须在 `dext_recommend` 顶层 `__init__.py` re-export（即 `from dext_recommend import StudentContext` 可用且 `dext_recommend.StudentContext is dext_grounded.StudentContext` 为真），不得仅在 `models.py` 内部 import。验收测试必须断言这一身份等价（`rec.StudentContext is grounded.StudentContext`），不得用“`RecommendRequest` 接受 grounded StudentContext”的行为断言替代——行为等价不能证明 re-export 契约。

**深度不可变（必须）**：`RecommendationFilters.university_ids`、`ConversationContext.prior_result_entity_ids`、`RecommendedProfessor.matched_topics`/`evidence_refs`/`short_reasons`、`RecommendResponse.results`/`warnings` 等集合字段必须以只读视图（`tuple` 或 `frozenset`）暴露，就地 `.append()` 必须报错；仅冻结 dataclass 本身（字段重赋值报错）不满足“标称不可变”。

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
- `content_policy_refusal`（命中内容政策时的推荐侧硬拒答）
- `political_sensitive` / `personal_attack` / `sexual_content` / `violent_content` / `mentor_attack`（grounded 层分类码；推荐侧可作为诊断/测试断言，但面向用户的终止码统一为 `content_policy_refusal`）

## 5. Ports

每个 port 是 `dext_recommend` 自己定义的抽象协议，方法签名稳定但本阶段无实现：

```text
ActiveSnapshotProvider
  get_snapshot() -> ActiveBuildSnapshot | null # 只返回最后一次完整验证的 snapshot

CatalogReleasePort
  async read_active() -> CatalogReleaseObservation | null
  async read_samples(build_id, sample_ids) -> tuple[ProfessorReleaseSample]

VectorReleasePort
  async read_current(alias, sample_ids) -> VectorReleaseObservation | null

GraphReleasePort
  async read_active(sample_ids) -> GraphReleaseObservation | null

RankingProfilePort
  async read_version(path) -> str

VectorSearchPort
  async hybrid_recall(snapshot, query_vector, filters, oversample, profile_version) -> list[VectorHit]
  async alias_readback(snapshot) -> AliasReadback
  async count_readback(snapshot, filter) -> int

ProfessorFactPort
  async get_detail(snapshot, entity_id, include_contacts, viewer_permissions) -> ProfessorDetail
  async hydrate(snapshot, entity_ids) -> dict[entity_id, ProfessorFact]

QueryEmbeddingPort
  async embed(snapshot, query_text) -> EmbeddingResult  # 含与 snapshot.embedding_fingerprint 比对

LLMGenerationPort
  async generate(...) -> GenerationResult  # re-export 共享契约，不在本模块重复定义
```

所有用户可见 AI 回复最终都必须经过共享 `ConstrainedGenerationPipeline`/`SafetyGuard`；本阶段只固化 re-export 与错误码，不允许在 `dext_recommend` 内复制一套内容安全规则或正则。

**两层端口边界**：R2 原始 readback ports 不得接收 `ActiveBuildSnapshot`，否则会形成“先有 snapshot 才能验证 snapshot”的循环依赖。R2 校验完成后通过 `ActiveSnapshotProvider` 暴露缓存 snapshot。业务数据端口 `hybrid_recall`/`hydrate`/`get_detail`/`alias_readback`/`count_readback`/`embed` 仍显式接收 `ActiveBuildSnapshot`，保证单次请求不混用新旧 build。

**异步边界**：所有可能执行文件、SQLite、Qdrant、Neo4j、embedding 或 LLM I/O 的 port 方法均为 `async def`，调用方必须 `await`。`ActiveSnapshotProvider.get_snapshot()` 仅执行进程内、无阻塞的原子缓存读取，保持同步；纯校验、过滤、排序、DTO 映射也保持同步。真实 adapter 不得在 async 方法内直接调用阻塞客户端；没有原生异步驱动时必须显式线程卸载并设置超时/并发上限。

调用方（recommend core / detail service / 生成服务）在请求入口从 provider 取一次 snapshot 并固定，全程传同一份；业务数据端口不得在内部自行刷新。R2 刷新失败时 provider 继续保留旧的已验证 snapshot，绝不合并半刷新状态。

`ProfessorDetail` 在本阶段定义为只读容器（阶段 4 填充组装逻辑）；`EmbeddingResult` 必须携带与 `ActiveBuildSnapshot.embedding_fingerprint` 比对所需的字段。

## 6. Fake ports

每个 port 提供一个 fake 实现，供后续阶段单测使用，不依赖真实外部服务：

- `FakeActiveSnapshotProvider`：可注入预设 snapshot 或 `null`。
- `FakeCatalogReleasePort` / `FakeVectorReleasePort` / `FakeGraphReleasePort` / `FakeRankingProfilePort`：可独立模拟缺 pointer/alias、三端 build 不一致、embedding 不一致、覆盖率不足与安全的 readback error。
- `FakeVectorSearchPort`：可注入预设 hits 与 readback 结果。
- `FakeLLMGenerationPort`：必须能返回 `content_policy_refusal` 及其分类 warning，供 R3/R5/R6 断言推荐侧不会在命中内容政策时继续召回、生成或交付半清洗输出。
- `FakeProfessorFactPort`：可注入预设 detail bundle。
- `FakeQueryEmbeddingPort`：返回固定向量与一致 fingerprint。

fake ports 只用于测试，不进入运行时 composition root。

## 7. 配置

`config.py` 从环境读取，所有超时、limit、ranking profile 路径配置化：

```text
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

**密钥字段类型（必须）**：`embedding_api_key`、`llm_api_key`、`neo4j_password` 必须用 `pydantic.SecretStr`（或等价的不序列化明文的类型），而不是普通 `str` + `Field(repr=False)`。`repr=False` 只影响 `repr()`，`model_dump()` 仍会输出明文，不能满足“API key 不出现在任何可序列化结构”。具体要求：

- `model_dump()`（含 `mode="json"`）、`model_dump_json()`、`dict(settings)` 等**任意**序列化路径都不得输出密钥明文；`SecretStr` 在这些路径下输出 `**********` 或被 `exclude` 掉。
- 不得仅依赖可选的 `safe_snapshot()` 辅助方法——`safe_snapshot()` 是便利方法，不是唯一防线；默认 `model_dump()` 本身就必须脱敏。
- 仅在显式取值（`settings.llm_api_key.get_secret_value()`）时才可拿到明文，且该取值不得出现在日志、exception repr、generation profile 或 manifest 中。

## 8. import 边界

`dext_recommend` 与 `dext`、`dext_graph`、`dext_monitor` 平级，**不 import** 这三者任何内部模块、ORM model、service class 或 CLI command。`dext_recommend` 与 `dext_competition` 互不 import，但都可 import 共享 grounded-generation 契约。

## 9. 验收标准

- 包结构按 §2 创建，`__init__.py` re-export 公开接口，模块形成无环 import DAG。
- §3 内部模型字段与 overview §8/§10 严格一致；`StudentContext`/`SourceRef` 从共享契约 import。
- §4 错误码枚举注册完成，结构化字段稳定。
- §5 已验证 snapshot provider、四个 R2 raw readback ports 与业务数据 ports 签名稳定，可被 fake 实现；raw ports 不接收 snapshot。
- §6 fake ports 可支撑 R2 缺 ACTIVE/alias/pointer/不一致/覆盖率不足测试以及后续核心逻辑单测。
- `test_import_boundary.py` 通过静态检查或 import 探针确认：`dext_recommend` 不 import `dext.*`、`dext_graph.*`、`dext_monitor.*`，且不 import `dext_competition.*`。
- 配置 key 命名稳定，API key 不出现在任何可序列化结构中。

**以下为面向序列化与可变性的攻击性测试（必须）**：

- **re-export 身份等价（必须）**：`assert dext_recommend.StudentContext is dext_grounded.StudentContext` 与 `assert dext_recommend.SourceRef is dext_grounded.SourceRef`，不得用“`RecommendRequest` 接受 grounded `StudentContext`”的行为断言替代。
- **配置默认序列化脱敏（必须）**：设了 `embedding_api_key`/`llm_api_key`/`neo4j_password` 后，`settings.model_dump()`、`settings.model_dump(mode="json")`、`settings.model_dump_json()` 的输出中都不得包含明文密钥（`SecretStr` 应序列化为 `**********` 或被排除）；不得仅断言 `safe_snapshot()` 脱敏。
- **深度不可变（必须）**：`RecommendedProfessor.matched_topics.append(...)`、`RecommendResponse.results.append(...)`、`ConversationContext.prior_result_entity_ids.append(...)`、`QueryUnderstanding.research_interests.append(...)` 及嵌套 payload/mapping 修改都必须报错，不是仅字段重赋值报错。
- **模块内全绿**：`uv run pytest tests/dext_recommend/ -q` 必须全绿。不要求全项目 `uv run pytest -q` 全绿（全项目超时属已知约束，不作为本阶段阻塞条件），但本模块内不得有失败或跳过。

async `ReadinessService.check`、同步 `ReadinessService.get_snapshot` 与 async `RecommendationCore.recommend` 的 `NotImplementedError` 是显式延后到 R2/R3 的占位，不计为本阶段缺陷。
