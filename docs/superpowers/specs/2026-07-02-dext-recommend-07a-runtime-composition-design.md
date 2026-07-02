# 阶段 7a：dext_recommend live runtime 与 production composition

> 状态：设计稿（详细）
>
> 前置依赖：R3d、R4b、R5/R6 所需 live ports 已实现；发布产物满足 readiness contract；
> `RecommendGenerationProfile.from_file` 已内置 `grounded_rules_manifest_hash` 校验。
>
> 内容安全增量：production root 必须在 startup 早期校验 recommendation generation profile
> 绑定当前 grounded rules manifest hash；缺失或不匹配时 fail-fast，且不得构造半可用 core。
>
> 后续阶段：[R7b HTTP/application state](2026-07-02-dext-recommend-07b-http-app-state-design.md)

## 1. 目标与范围

把 R3/R4/R5/R6 已完成的进程内能力接成一个可启动、可关闭、fail-fast 的 live runtime。
R7a **不做** HTTP、OpenAPI DTO、auth/identity、PostgreSQL application state、stream/chat 持久化
（留给 R7b）。

R7a 交付：

- `RecommendationCore` / `ConversationDispatcher` / `AuxiliaryGenerationService` 的 production 装配
- live `ActiveSnapshotProvider` / `QueryEmbeddingPort` / `VectorSearchPort` / `ProfessorFactPort` /
  `LLMGenerationPort`（core 与 aux 各一份独立 client）/ live `RecommendGenerationProfilePort`
- startup readiness + generation profile manifest hash 校验
- refresh / shutdown 生命周期

唯一 production 入口：

```python
async def build_live_recommendation_runtime(
    settings: RecommendSettings | None = None,
    *,
    clients: LiveClients | None = None,
) -> LiveRecommendationRuntime
```

`clients` 是注入 seam：生产传 `None`（builder 用 settings 构造真实 client）；
测试传 fake SDK client（`FakeEmbeddingClient` / `FakeQdrantClient` / `FakeNeo4jDriver` /
`FakeLLMClient`）。所有 live adapter 都以 client 作为构造参数，adapter 本身从不被 monkeypatch。

## 2. 模块布局

| 文件 | 动作 | 职责 |
|------|------|------|
| `src/dext_recommend/runtime.py` | 新增 | `LiveRecommendationRuntime` + `LiveClients` + `build_live_recommendation_runtime()` — 唯一 production root |
| `src/dext_recommend/adapters/active_snapshot.py` | 新增 | `LiveActiveSnapshotProvider` — 包装 `ReadinessService`，refresh task，stale fail-closed |
| `src/dext_recommend/adapters/query_embedding.py` | 新增 | `LiveQueryEmbeddingAdapter` — 专用 `AsyncOpenAI`，snapshot-pinned model/provider/dimension/fingerprint 校验 |
| `src/dext_recommend/adapters/qdrant_search.py` | 新增 | `LiveVectorSearchAdapter` — pinned physical collection，dense-only，alias/count readback |
| `src/dext_recommend/adapters/generation_profile.py` | 新增 | `LiveGenerationProfileAdapter` — `read_profile(path)` → `RecommendGenerationProfile.from_file`（当前仅有 Fake） |
| `src/dext_recommend/config.py` | 修改 | 新增 runtime-lifecycle / embedding / vector / llm 调优字段 |
| `src/dext_recommend/errors.py` | 修改 | 新增 `RecommendationRuntimeError` |
| `src/dext_recommend/__init__.py` + `adapters/__init__.py` | 修改 | re-export 新公开符号 |
| `tests/dext_recommend/test_recommend_runtime_composition.py` | 新增 | startup fail-fast / success（injected fakes） |
| `tests/dext_recommend/test_recommend_runtime_shutdown.py` | 新增 | aclose 顺序、不泄漏 task/client |
| `tests/dext_recommend/test_recommend_active_snapshot_provider.py` | 新增 | refresh / stale / refresh-failure-preserves / shutdown |
| `tests/dext_recommend/test_recommend_query_embedding_adapter.py` | 新增 | injected fake AsyncOpenAI；model/dim mismatch；日志不含 query/key |
| `tests/dext_recommend/test_recommend_qdrant_search_adapter.py` | 新增 | injected fake Qdrant；pinned collection；alias switch；payload filter；count；vector_unavailable |
| `tests/dext_recommend/test_recommend_runtime_live_integration.py` | 新增，mark=integration | 真 Qdrant/Neo4j/embedding；默认 skip |
| `tests/dext_recommend/test_recommend_config.py` | 修改 | 追加新字段的 secret-exclusion 断言 |
| `tests/dext_recommend/test_recommend_errors.py` | 修改 | `RecommendationRuntimeError` 构造 / `__str__` |

`composition.py` 的 `assemble_core` / `build_test_core` 保持不变（R3–R6 单测用 fakes 仍走它）；
新 `runtime.py` 是 production 路径，最终也经 `assemble_core(deps, settings)` 装配 core。

## 3. LiveRecommendationRuntime 与 startup 序列

```python
@dataclass(slots=True)
class LiveRecommendationRuntime:
    core: RecommendationCore
    conversation: ConversationDispatcher
    auxiliary_generation: AuxiliaryGenerationService
    readiness: LiveActiveSnapshotProvider
    generation_profile: RecommendGenerationProfile   # startup-validated, immutable

    async def aclose(self) -> None: ...
```

`LiveClients` 是一个 dataclass，字段：`openai_embedding`、`qdrant_client`、`neo4j_driver`、
`openai_llm_core`、`openai_llm_aux`。生产路径 `None` → builder 构造真实 client。

**Startup 序列（每步 fail-fast）：**

1. **加载 generation profile**：`RecommendGenerationProfile.from_file(settings.generation_profile_path)`。
   `from_file` 已校验 `grounded_rules_manifest_hash == load_grounded_rules().manifest_hash`
   （不匹配抛 `ValueError`）。捕获 → `RecommendationRuntimeError(code="generation_profile_manifest_mismatch")`。
   文件缺失/非法 → `code="generation_profile_unavailable"`。
   manifest hash 校验在**任何网络调用之前**，确保 stale 内容政策 profile 最快失败。
2. **构造 live clients**（或使用注入的）：embedding `AsyncOpenAI`、core/aux LLM 各一份独立 `AsyncOpenAI`
   实例（共享同一 generation_profile，client 独立）、`AsyncQdrantClient`、`AsyncGraphDatabase.driver(...)`。
3. **构造 release adapters**：
   - `CatalogReleaseAdapter(CatalogSqliteReader(settings.catalog_path), sample_size=settings.readiness_sample_size)`
   - `VectorReleaseAdapter(QdrantReader(qdrant_client, embedding_dimension=..., embedding_fingerprint=..., payload_schema_version=settings.qdrant_payload_schema_version))`
     （dimension/fingerprint 在 readiness 成功后由 snapshot 提供校验，构造期用占位值即可，
     因为 QdrantReader 只在 `read_current` 时用这些值做 sample 比对）
   - `GraphReleaseAdapter(Neo4jReader(neo4j_driver))`
   - `RankingProfileAdapter()`
   - `LiveGenerationProfileAdapter()`
4. `ReadinessDeps(catalog_port, vector_port, graph_port, ranking_port)` → `ReadinessService(deps, settings)`。
5. `LiveActiveSnapshotProvider(readiness, settings)` — `__init__` 同步，**不**做检查。
6. **`await asyncio.wait_for(snapshot_provider.start(), settings.runtime_startup_timeout)`** —
   跑一次 `readiness.check()`；若 `not report.ready or report.snapshot is None` →
   `RecommendationRuntimeError(code="readiness_failed", retryable=True)`（readiness 失败通常瞬态：
   Qdrant 宕机、build 未 promote）。catalog 缺失 / Qdrant alias 缺失 / Neo4j pointer 缺失均
   表现为 readiness ERROR code，归到此错误。
7. 从 readiness report 派生 `coverage_flags_by_build_id`：
   `{snapshot.build_id: {"org_unit_ids": stat.passes, "profile_hash": ..., "role_status": ..., "eligibility": ...}}`
   — startup 时冻结，仅 snapshot.build_id 一条。
8. 构造 request-path adapters：`LiveQueryEmbeddingAdapter(client=embedding_client, settings=settings)`、
   `LiveVectorSearchAdapter(client=qdrant_client, settings=settings)`、
   `CatalogProfessorFactAdapter(CatalogSqliteFactReader(settings.catalog_path), settings=settings)`。
9. 两份 LLM adapter：`OpenAICompatibleLLMGenerationAdapter.from_settings(settings, profile)` —
   独立实例（core 与 aux）。
10. `deps = RecommendDeps(snapshot_port=snapshot_provider, embedding_port=..., vector_port=...,
    facts_port=..., llm_port=core_llm, ranking_port=..., generation_profile_port=...,
    coverage_flags_by_build_id=coverage_flags)`。
11. `core = assemble_core(deps, settings)`；
    `conversation = ConversationDispatcher(core, ConstrainedGenerationPipeline(core_llm), settings)`；
    `aux = AuxiliaryGenerationService(core=core, pipeline=ConstrainedGenerationPipeline(aux_llm), settings=settings)`。
12. 返回 `LiveRecommendationRuntime(...)`。返回前**最后一步**启动 snapshot refresh 后台 task。

**`aclose()` 逆序**：cancel+await refresh task → aux LLM client.close → core LLM client.close →
embedding client.close → Qdrant client.close → Neo4j driver.close。每个 close 独立 try/except，
一个失败不中断其余。幂等（第二次调用 no-op）。

`retryable` 语义：`readiness_failed` / `embedding_unavailable` / `vector_unavailable` 为
`retryable=True`（operator 可重试 startup）；`generation_profile_manifest_mismatch` /
`generation_profile_unavailable` / `embedding_model_mismatch` / `embedding_dimension_mismatch` /
`embedding_provider_mismatch` 为 `retryable=False`（配置问题，需改配置/重建）。

## 4. LiveActiveSnapshotProvider

满足现有 `ActiveSnapshotProvider` Protocol（同步 `get_snapshot() -> ActiveBuildSnapshot | None`）。

**状态：**
- `_readiness: ReadinessService`
- `_settings`
- `_cached: ActiveBuildSnapshot | None` — 最后一次完整通过的 snapshot
- `_cached_at: float` — 最后一次成功 refresh 的 monotonic 时间
- `_last_report: ReadinessReport | None` — startup 时供 coverage_flags 注入
- `_refresh_task: asyncio.Task | None`
- `_closed: bool`
- `_refresh_lock: asyncio.Lock` — 串行化后台 refresh，慢的不会叠在快的上

**`start()`（async，root 调一次）：** `await self._refresh_once()`；成功则 `_cached` 已设；
失败则向上抛（root 包成 `RecommendationRuntimeError`）。**不**在此启动后台 loop —
root 在 `start()` 返回后才启动 loop，确保失败的 start 不留悬挂 task。

**`_refresh_once()`（async，private）：**
```
report = await self._readiness.check()     # 内部已 lock 串行
self._last_report = report
if report.ready and report.snapshot is not None:
    self._cached = report.snapshot
    self._cached_at = monotonic()
return report
```

关键：`_cached` **仅在 `report.ready` 时**赋值。失败的 refresh 不动 `_cached`，
provider 的契约自洽——"最后一次通过完整 readiness 的 snapshot"——不依赖
`ReadinessService` 内部保留旧 snapshot 的实现细节（`ReadinessService.check()` 在失败时
保留其内部 `_snapshot`，但 provider 不据此判定，而是用自己 `_cached`）。

**`get_snapshot()`（同步）— fail-closed 闸门：**
```
if self._closed: return None
if self._cached is None: return None
age = monotonic() - self._cached_at
if age > self._settings.runtime_snapshot_max_age: return None   # stale → fail closed
return self._cached
```
返回 `None` 沿用 `ConversationDispatcher` / `RecommendationCore` 现有
`ACTIVE_BUILD_UNAVAILABLE` 路径——不新增 error code、不破坏 sync port 契约。

**后台 refresh loop（root 在 `start()` 后启动）：**
```
while not self._closed:
    await sleep(settings.runtime_refresh_interval)
    if self._closed: break
    try:
        async with self._refresh_lock:
            await self._refresh_once()        # 失败只记日志，不抛
    except Exception as exc:
        log.warning("readiness refresh failed: %s", exc)   # exc 不含 secret
```
refresh 失败不覆盖 `_cached`；旧 snapshot 继续服务直到 stale。

**`last_report()`（同步）：** 返回 `_last_report`，startup 时 root 用它构建 `coverage_flags_by_build_id`。

**`aclose()`（async）：** `_closed=True`，cancel `_refresh_task`，await（swallow）；
此后 `get_snapshot()` 返回 `None`。

**测试点：** start 时无 ACTIVE → `start()` 抛；start ready → `get_snapshot()` 返回；
refresh 失败不动 `_cached`；refresh 成功替换 `_cached` 并重置 `_cached_at`；
超过 `runtime_snapshot_max_age` 后 `get_snapshot()` 返回 `None`；`aclose()` 取消 task、
无 pending refresh 泄漏（teardown 用 `asyncio.all_tasks()` 断言）。

## 5. LiveQueryEmbeddingAdapter

专用 `AsyncOpenAI`（root 从 `settings.embedding_base_url` + `embedding_api_key` 构造，或注入）。
实现 `QueryEmbeddingPort.embed(snapshot, query_text) -> EmbeddingResult`。

```python
class LiveQueryEmbeddingAdapter:
    def __init__(self, *, client: Any, settings: RecommendSettings) -> None: ...
    async def embed(self, snapshot, query_text) -> EmbeddingResult: ...
    async def aclose(self) -> None: ...
```

**`embed()` 契约（严格，snapshot-pinned）：**

1. **Model pin**：`settings.embedding_model` 必须等于 `snapshot.embedding_model`。
   不匹配 → `RecommendationRuntimeError(code="embedding_model_mismatch", retryable=False)`。
2. **Provider check**：`settings.embedding_provider` 非空且等于 `snapshot.embedding_provider`。
   不匹配 → `code="embedding_provider_mismatch", retryable=False`。
3. `await asyncio.wait_for(client.embeddings.create(model=settings.embedding_model,
   input=[query_text], encoding_format="float"), settings.embedding_timeout)`。
   `embedding_max_retries` 控制 `APITimeoutError`/`APIConnectionError` 的重试（默认 1 次）。
4. 取 `response.data[0].embedding` → `tuple(float(x) for x in ...)`。
5. **Dimension pin**：`len(vector) == snapshot.embedding_dimension`。
   不匹配 → `code="embedding_dimension_mismatch", retryable=False`。
6. **Fingerprint pin**：`embedding_fingerprint=snapshot.embedding_fingerprint`，供下游
   （Qdrant search payload、response envelope）pin。
7. 返回 `EmbeddingResult(vector=..., embedding_fingerprint=snapshot.embedding_fingerprint, sparse_vector=None)`。

**错误分类：** `APITimeoutError`/`APIConnectionError`/`httpx.RequestError` 及 `asyncio.TimeoutError`
→ `code="embedding_unavailable", retryable=True`。`APIStatusError`（4xx/5xx）→ 同 code，
`retryable` 仅 429/503/504 为 true。这些在请求时冒泡为既有 `embedding_unavailable` recommendation error。

**日志规则：** 仅记 `embedding_model`、`embedding_fingerprint`、`len(vector)`、latency、error code。
**禁止**记 `query_text`、vector、api key。`settings.safe_snapshot()` 已排除
`embedding_api_key`/`llm_api_key`/`neo4j_password`，新字段保持该排除（测试断言）。

**Dense-only 说明：** `sparse_vector=None` 恒定。`EmbeddingResult` 保留 `sparse_vector` 字段
（frozen mapping 或 None），将来确认 sparse schema 后端口签名不变，仅 adapter 增 sparse 构造。
R7a 在 readiness 时记 "dense-only" 能力标记（Qdrant adapter 在 readback 时检查 collection
sparse 配置并记日志）。

**测试点（injected fake AsyncOpenAI）：** 正常 → vector tuple + 正确 fingerprint；
model mismatch → 结构化 error；provider 未设 → 结构化 error；dimension mismatch → 结构化 error；
client timeout → `embedding_unavailable`；断言 captured log 不含 `query_text`/api key；
`aclose()` 关 client。

## 6. LiveVectorSearchAdapter

专用 `AsyncQdrantClient`（root 从 `settings.qdrant_url`/`qdrant_timeout` 构造，或注入）。
实现 `VectorSearchPort.hybrid_recall / alias_readback / count_readback`。

```python
class LiveVectorSearchAdapter:
    def __init__(self, *, client: Any, settings: RecommendSettings) -> None: ...
    async def hybrid_recall(self, snapshot, query_vector, filters, oversample, profile_version, *, rrf_k, sparse_vector=None) -> list[VectorHit]: ...
    async def alias_readback(self, snapshot) -> AliasReadback: ...
    async def count_readback(self, snapshot, filter=None) -> int: ...
    async def aclose(self) -> None: ...
```

**核心 pinning 规则：** 每个请求使用 snapshot 已解析的**物理 collection**
`snapshot.qdrant_alias_target`，绝不中途重新解析 alias。alias 由 readiness 解析
（捕获 `target_collection` 进 snapshot）；`hybrid_recall` 直接用 `snapshot.qdrant_alias_target`
作为 Qdrant collection name。请求中途 alias 切换对在飞请求无影响。

**`hybrid_recall()`（dense-only）：**
1. `collection = snapshot.qdrant_alias_target`。空 →
   `RecommendationRuntimeError(code="vector_unavailable", retryable=True)`。
2. 构造 Qdrant filter：`_build_filter(snapshot, filters)`，含强制 pin：
   - `build_id == snapshot.build_id`（payload pin，防 alias 被 repoint 到不同 build 数据）
   - `payload_schema_version == snapshot.qdrant_payload_schema_version`
   - `role_status` / `master_eligibility` / `org_unit_ids` 来自 `RecommendationFilters`
     （coverage flag false 时 org_unit filter 降级）
3. `await asyncio.wait_for(client.query_points(collection_name=collection,
   query=query_vector, using=<dense vector name>, limit=oversample, query_filter=filter,
   with_payload=True, with_vectors=False), settings.qdrant_timeout)`。
   - 本版 dense-only：`sparse_vector` 入参接受但忽略（startup 时记一次"dense-only"日志）。
   - **实现期确认项**：Qdrant collection 使用 named vector 还是 default unnamed vector？
     实现时查 build sink 确定并 pin name；unnamed 则 `using=None`。
4. 映射 → `VectorHit(entity_id=str(p.id), score=float(p.score), payload=dict(p.payload or {}))`。
   payload 由 `VectorHit.__post_init__` freeze。
5. Qdrant exception/timeout → `code="vector_unavailable", retryable=True`。

**`alias_readback()`（readback，非请求路径）：** 解析 `settings.qdrant_alias` →
返回 `AliasReadback(alias, target_collection, build_id, payload_schema_version)`。
供 readiness 使用，并供请求路径选做诊断：`hybrid_recall` 首用时可选断言
`alias_readback(snapshot).target_collection == snapshot.qdrant_alias_target`，
diverge 时记日志。这是**诊断**而非硬 fail——请求仍用 pinned 物理集合，故无论 alias 是否漂移都正确；
日志告知 operator alias 自 snapshot pin 后已移动。

**`count_readback()`（exact）：** `await client.count(collection_name=snapshot.qdrant_alias_target,
count_filter=<同 filter>, exact=True)` → `int`。若请求 filter 非空，同带 `build_id == snapshot.build_id` pin。
exact count（Qdrant `exact=True`）。

**`aclose()`：** 关 client（`AsyncQdrantClient.close()` 同步，需 guard）。

**filter 翻译关注：** `RecommendationFilters` → Qdrant `Filter`/`FieldCondition`。
纯可映射逻辑，但需小心 `org_unit_ids`（any-of）、`role_status` 排除
（`excluded`/`review` gating）、coverage 降级路径。filter builder 作为 adapter 的私有
`_build_filter(snapshot, filters)` 方法；它是 R7a 新增真实逻辑，单独单测。

**测试点（injected fake Qdrant client）：** 用 `snapshot.qdrant_alias_target` 而非
`settings.qdrant_alias`；fake 中途 alias 切换不改被查 collection；`payload.build_id != snapshot.build_id`
的 hit 被过滤掉（fake 返回 poisoned point，adapter 必须丢弃）；`role_status=excluded` 被排除；
`count_readback` 返回 exact count；Qdrant exception → `vector_unavailable`；`aclose()` 关 client。

## 7. 配置增量、错误类型、日志规范

**`RecommendSettings` 新增字段（`config.py`）：**

```python
# Runtime lifecycle
runtime_refresh_interval: float = Field(default=60.0, gt=0.0)
runtime_snapshot_max_age: float = Field(default=300.0, gt=0.0)
runtime_startup_timeout: float = Field(default=30.0, gt=0.0)   # awaits readiness.start() under this

# Embedding live adapter
embedding_base_url: str = ""
embedding_timeout: float = Field(default=10.0, gt=0.0)
embedding_max_retries: int = Field(default=1, ge=0, le=5)

# Vector search live adapter
qdrant_timeout: float = Field(default=10.0, gt=0.0)
qdrant_query_limit_max: int = Field(default=1000, ge=1)
qdrant_payload_schema_version: int = 2   # compared against snapshot

# Neo4j
neo4j_max_connection_lifetime: float | None = None

# LLM separation (core 与 aux client 独立；此为 per-call timeout floor)
llm_timeout: float = Field(default=20.0, gt=0.0)
```

`embedding_model` / `embedding_provider` / `embedding_dimension` **不**重新添加——
它们来自 snapshot，不来自 settings。`settings.embedding_model` 作为"配置 model"用于与 snapshot 比对
（§5 step 1），且已存在于 `RecommendSettings`。

`safe_snapshot()` 保持现有排除 `{"embedding_api_key", "llm_api_key", "neo4j_password"}`——
新字段均非 secret。一个新测试断言 secret-exclusion 在新字段后仍成立。

**`RecommendationRuntimeError`（`errors.py`，独立于 request-level `RecommendationError`）：**

```python
@dataclass(frozen=True, slots=True)
class RecommendationRuntimeError(RuntimeError):
    code: str          # "readiness_failed" | "generation_profile_manifest_mismatch"
                       # | "generation_profile_unavailable" | "embedding_model_mismatch"
                       # | "embedding_provider_mismatch" | "embedding_dimension_mismatch"
                       # | "embedding_unavailable" | "vector_unavailable"
    message: str       # safe operator message — no secrets/query/embedding
    retryable: bool = False
    def __post_init__(self): super().__init__(self.message)   # so str() works
```

`__all__` 导出。它**不是** `RecommendationError`（后者带 `severity`，服务 request 响应；
runtime startup 失败面向 operator）。

**日志 allowlist / denylist（adapter 设计 + 一个审计测试 enforce）：**

| 允许 | 禁止 |
|------|------|
| `build_id`、`ranking_profile_version`、`generation_profile_version`、`embedding_fingerprint`、phase latency、error `code`、dependency name | query 原文、`StudentContext` 原文、embedding vector、contacts、API key/token/password、raw prompt、raw LLM output、被 content-policy 拒绝的原文 |

content-policy 拒绝日志规则（只记 `content_policy_refusal` + 分类 code + operation + action，
不记被拒原文或原始 LLM 输出）**已在 `dext_grounded.SafetyGuard` 实现**——R7a 不复制它；
pipelines 用默认 `SafetyGuard()`，不装配第二套规则（spec §2：不得在 production composition
中装配第二套规则）。

## 8. 测试策略与 TDD 顺序

TDD 顺序（避免大爆炸；每文件 RED→GREEN→commit）：

1. `test_recommend_runtime_composition.py` — startup fail-fast/success（injected fakes）。先写，pin root 契约。
2. `test_recommend_active_snapshot_provider.py` — refresh / stale / refresh-failure-preserves / shutdown。
3. `test_recommend_query_embedding_adapter.py` — injected fake AsyncOpenAI。
4. `test_recommend_qdrant_search_adapter.py` — injected fake Qdrant client。
5. `test_recommend_runtime_shutdown.py` — close 顺序、不泄漏 task/client。
6. `test_recommend_config.py` — 追加新字段 secret-exclusion 断言。
7. `test_recommend_errors.py` — `RecommendationRuntimeError` 构造 / `__str__`。
8. 最后：`uv run pytest tests/dext_recommend -q --tb=short`。本地 Qdrant/Neo4j/embedding 可用时
   再跑 `integration`-marked 文件。

**注入 seam 回顾：** `build_live_recommendation_runtime(settings=None, *, clients: LiveClients | None = None)`。
测试构造 `LiveClients(openai_embedding=FakeEmbeddingClient(), qdrant_client=FakeQdrantClient(),
neo4j_driver=FakeNeo4jDriver(), openai_llm_core=FakeLLMClient(), openai_llm_aux=FakeLLMClient())` 传入。
生产传 `None` → builder 构造真实 client。adapter 类以 client 为构造 kwarg，adapter 本身从不被 monkeypatch。

**fake clients** 是 test 文件内小 helper（`FakeEmbeddingClient.embeddings.create(...)` 返回 canned
`Embedding`；`FakeQdrantClient.query_points/count/get_aliases/close(...)`）。它们放在 test 文件，
不放在 `ports/_fakes.py`（那些 fake 实现 *ports*；这些 fake 实现 *SDK clients*，下一层）。

**integration tests**（`test_recommend_runtime_live_integration.py`，`@pytest.mark.integration`，
默认 skip）：起真实 runtime 打本地 Qdrant/Neo4j/embedding，断言一次 embed + 一次 hybrid_recall round-trip。
安全网，非 gate——gate 是 fake-client 套件 + 现有 recommend 套件保持 green。

## 9. 验收清单映射（spec §13）

| 要求 | 落点 |
|------|------|
| `build_live_recommendation_runtime(settings)` 是唯一 production root | §2、§3；`runtime.py` |
| startup 跑 readiness | §3 step 6、§4 `start()` |
| 校验 ranking + generation profile | §3 step 1（gen profile via `from_file` 含 manifest hash）+ readiness 校验 ranking version |
| 校验 generation profile manifest hash | §3 step 1；`RecommendGenerationProfile.from_file` 已强制 |
| 缺 catalog/Qdrant/Neo4j/profile/hash mismatch 全 fail-fast | §3 steps 1、6 |
| `RecommendationCore` 可用，无首次请求才 `NotImplementedError` | §3 — 所有 deps 构造期即 live |
| `ConversationDispatcher` 可用 | §3 step 11 |
| `AuxiliaryGenerationService` 可用 | §3 step 11 |
| refresh 成功才替换，失败保留旧 snapshot | §4 |
| stale snapshot fail-closed | §4 `get_snapshot()` → None |
| 请求用 pinned snapshot，alias 中途切换不混用 | §6 pinning 规则 |
| shutdown 不泄漏 task/client/driver | §3 `aclose()` 逆序；§6 `aclose()`；test 5 |
| 推荐域全量测试通过 | test step 8 |

**已知风险：** Qdrant filter 翻译（`_build_filter`）是 R7a 唯一非平凡新逻辑。
它是最可能出现 payload-build-mismatch bug 的地方（spec bug-trap list）。保持为纯函数，
在 adapter test 旁单独单测。

## 10. 韧性与观测补充

- 每个外部依赖独立 timeout、并发上限、错误分类；不共享全局 mutable request state。
- readiness refresh 失败保留最后一个完整 snapshot，超过 `runtime_snapshot_max_age` 后停止接收新请求（`get_snapshot()` 返回 None）。
- 记录 build/profile/fingerprint、phase latency、错误码与 pool saturation；
  禁止记录 query 原文、embedding、联系人、API key。
- content policy 命中只记录 `content_policy_refusal`、分类 code、operation 与 action；
  禁止记录被拒绝原文或原始 LLM 输出（已由 `SafetyGuard` 实现）。

## 11. 实现期确认项（非阻塞设计，实现时落实）

1. Qdrant collection 使用 named vector 还是 default unnamed vector？查 build sink 确定并 pin。
2. `--run-integration` convention 是否已存在于 conftest？若无则新增 marker 注册。
3. `QdrantReader` 构造期占位 `embedding_dimension`/`embedding_fingerprint` 的处理——readiness 成功后
   snapshot 提供权威值，确认 QdrantReader 在 `read_current` 时用 snapshot 值而非占位值比对。
