# dext_recommend 服务端可观测性设计 (serving observability)

> 状态：设计稿
>
> 日期：2026-07-02
>
> 目标模块：`dext_recommend/observability/`
>
> 前置依赖：R3–R5 已落地（`RecommendExecutionContext.phase_diagnostics`、`QueryDiagnostics`、结构化 error/warning code 已存在）；与 R7a runtime composition 同期接线，HTTP 面在 R7b 挂载
>
> 后续阶段：本稿为独立可观测性 spec，落地后供 [R7c production acceptance](2026-07-02-dext-recommend-07c-production-acceptance-design.md) canary 门禁消费

## 1. 目标与范围

为 `dext_recommend` 补齐**服务端运行时可观测性**：进程内指标计数、按时间窗口的请求事件、阶段延迟分解、召回漏斗、SLO 信号面与受控 AI trace 采样。填补 specs §18（隐私与日志）、R7a §4（韧性与观测）、R7c §3–§5（E2E/故障注入/发布门禁）要求但当前无人实现的缺口。

本稿严格服从以下边界（来自 overview §3、§6.3、§7）：

- `dext_recommend/observability/` 只从 `dext_recommend` 内部模块 import，**不 import** `dext`、`dext_graph`、`dext_monitor`。
- 不写 catalog SQLite、Qdrant、Neo4j、PostgreSQL；只读自身进程内 `MetricsRegistry`。
- aiohttp 不进入 `core/`；HTTP 面放在 `observability/server.py`，由 R7a composition root 装配。
- monitor 是**被动信号面**：常规只暴露 `/health`、`/metrics`、`/slo`；`/trace` 是默认关闭的调试端点，只有显式开关 + secret + 内部鉴权同时满足时才注册/返回。不触发回滚，不参与发布决策（R7c canary controller 消费 `/slo` 后自行决定）。
- `/readiness` 不归本稿：仍是 R7a `ActiveSnapshotProvider` 的 ACTIVE build 一致性职责，不混入 SLO。
- 绑定安全不靠注释兜底：若作为 sidecar 独立监听，默认只绑定 `127.0.0.1`；若挂到对外 aiohttp app，同一组路由必须经过内部鉴权/运维网关，不能仅宣称“localhost-only”。

**范围裁剪（v1 非目标）**：

- 跨实例指标聚合（单进程 v1，重启丢失）。
- Prometheus / OTLP exporter（v1 只输出 JSON）。
- 结构化应用日志格式改造（独立后续工作）。
- 离线 eval 偏差面（归 `dext_recommend/eval/`）。
- 自动回滚触发（归 R7c canary controller）。
- `/readiness` 修改（归 R7a）。

## 2. 架构与放置

新增 `src/dext_recommend/observability/` 子包，与 `core/`、`ports/`、`adapters/` 平级：

```text
src/dext_recommend/observability/
  __init__.py            # 重新导出公共接口 (__all__)
  settings.py            # ObservabilitySettings dataclass (RecommendSettings 字段，DEXT_RECOMMEND_* 前缀)
  metrics.py             # MetricsRegistry: 原子计数器 + 请求滚动窗口 + 阶段延迟 ring buffer + 召回漏斗
  recorder.py            # Recorder: 核心调用的 void no-throw 接口 + RequestDigest + 白名单序列化
  ai_trace.py            # DEXT_RECOMMEND_AI_TRACE 三重门控采样器 (默认无文本预览)
  server.py              # ObservabilityServer: aiohttp 只读 GET, 挂 /api/rec/*
```

**三个角色，单向数据流：**

1. **`MetricsRegistry`** — runtime composition root 创建的单例，持有原子计数器、请求滚动窗口与 ring buffer。唯一 mutable state。
2. **`Recorder`** — 核心调用的 void 接口（`record_response(...)` / `record_llm_call(...)` / `record_policy_refusal(...)`），注入 `RecommendDeps`。永不抛异常、永不阻塞请求路径（写入为 in-process 原子操作）。测试中可为 `None`/fake。
3. **`ObservabilityServer`** — 只读 aiohttp app，从 registry 读取并暴露 `/api/rec/*`。仅在 R7b/R7c 接线（HTTP 层归 R7b）；registry 与 recorder 从 R7a 起即存在，HTTP 之前指标也可在进程内测试中累积。

**装配**：`build_live_recommendation_runtime(settings)`（R7a composition root）构造 `MetricsRegistry`、包成 `Recorder`、附到 `RecommendDeps`，并在 R7b 后可选地启动 `ObservabilityServer`。fake ports 单元测试不变（recorder 默认 `None`，core 用 `if recorder is not None` 短路）。

**边界合规**：`observability/` 不导入 `dext_monitor`，不跨包共享内部类。它读取的外部事实只能来自自身进程内 registry 与 R7a 注入的只读 runtime status seam（仅读当前 snapshot/readiness 元数据，不发起新的 snapshot fetch / I/O）。当前 `ActiveSnapshotProvider` 只有 `get_snapshot()`，不足以表达 `readiness_last_ok`、refresh 失败和 stale 状态，因此本稿明确新增只读 status seam，而不是从 snapshot created_at 猜测。

## 3. 指标注册表 (MetricsRegistry)

单进程事件循环内访问；计数器与 ring buffer 用单个 `threading.Lock`（适配 `asyncio.to_thread` 写入，KISS，不用无锁原语）。`/metrics` 可读累计指标；`/slo` 只能从带时间戳的请求滚动窗口计算，不能从进程启动以来的累计 counter 反推。

### 3.1 请求级计数器（始终开启，summary 级）

这些 counter 是进程生命周期累计值，重启清零；用于趋势和诊断，不直接作为 canary SLO 分母。

| 计数器 | 维度 | 来源 |
|---|---|---|
| `requests_total` | `route_intent`, `outcome`(`ok\|warn\|error`) | recommend/dispatcher 入口/出口 |
| `errors_total` | `error_code`(`RecommendationErrorCode`) | `ClassifiedRecommendError`、terminal 分支 |
| `warnings_total` | `warning_code` | `RecommendationWarning.code` |
| `content_policy_refusals_total` | `operation`(`recommend\|conversation\|detail\|match\|email\|compare`) | `SafetyGuard` 命中 |
| `permission_denials_total` | `kind`(`contacts\|review\|debug`) | `UNAUTHORIZED_*` 分支 |
| `permission_leakage_total` | `kind`(`contacts\|review\|debug\|cross_owner`) | 攻击测试或 runtime invariant 检测到的泄露；硬门禁必须为 0 |
| `empty_results_total` | — | `NO_CANDIDATES_AFTER_FILTERS` 且 `returned_count == 0` |
| `underfilled_results_total` | — | `0 < returned_count < limit` |
| `timeout_total` | `phase` | `asyncio.TimeoutError` 分支 |
| `llm_calls_total` | `operation`, `outcome` | query-understanding/intent/generation |
| `external_dependency_errors` | `dep`(`qdrant\|neo4j\|embedding\|llm\|catalog`) | `_guarded_*` 分类 |
| `pool_saturation_samples` | `dep`, `pool` | R7a live adapters 可选上报（无 adapter 上报则为空） |

### 3.2 SLO 请求滚动窗口

新增 `RequestEvent` ring/deque（按 `slo_window_s` 时间裁剪，默认 300s；容量上限可配，默认 4096，超出丢最旧）：

```python
@dataclass(frozen=True, slots=True)
class RequestEvent:
    request_id: str                 # 服务端生成或 HTTP 层传入的 opaque ID，不含用户输入
    observed_at_monotonic_s: float
    observed_at_utc: str
    build_id: str | None
    ranking_profile_version: str | None
    generation_profile_version: str | None
    embedding_fingerprint: str | None
    taxonomy_version: str | None
    route_intent: str | None
    outcome: str                    # ok|warn|error
    total_latency_s: float
    error_code: str | None
    warning_codes: tuple[str, ...]
    timeout: bool
    empty_result: bool
    underfilled_result: bool
    content_policy_refusal: bool
    permission_denial_kind: str | None
    permission_leakage_kind: str | None
```

`/slo` 的 `error_rate`、`timeout_rate`、`empty_result_rate`、`p95_total_latency_s` 等只从当前窗口内的 `RequestEvent` 计算。正确的 fail-closed 权限拒绝只进入 `permission_denial_kind`，不得等同于泄露；泄露由 `permission_leakage_kind` 表示，硬门禁为 0。

### 3.3 阶段延迟 ring buffer

每请求 phase 耗时分解。阶段名直接来自 `RecommendExecutionContext.phase_diagnostics`（`core/_resilience.py` 已存在的 `ctx.record(phase, elapsed_ms, error_code)` 机制）——recorder 读取该 tuple，不重复计时。

**当前已发出 `PhaseDiagnostic` 的阶段**（来自 `_guarded_async`/`_guarded_sync` 调用点与 `detail_fetch.py` 的 `ctx.record`）：

| phase 名 | 来源 |
|---|---|
| `snapshot` | `_guarded_sync` (`_recommend_with_pins`) |
| `generation_profile` | `_guarded_async` |
| `ranking_profile` | `_guarded_async` |
| `query_understanding` | `_guarded_async` |
| `anchor_hydrate` | `_guarded_async`（仅 `same_field` 路由） |
| `embedding` | `_guarded_async` |
| `vector_recall` | `recall_loop` 内每个 oversample step 的向量召回 |
| `candidate_hydrate` | `recall_loop` 内每个 oversample step 的候选 hydration |
| `details` | `detail_fetch.py` 的 `ctx.record`（per-entity，非 per-request） |

`recall`、`rerank`、`cards`、`validation` 当前**未**经 `ctx.record` 发出耗时。本稿落地时 recorder 先消费上述已存在的 phase tuple；补齐 `recall`/`rerank`/`cards`/`validation` 的 wrapper `ctx.record` 是本稿的一项**实现任务**（在既有路径上各加一次 `ctx.record`，不引入新分支），以便 `/metrics` 覆盖 spec §18 要求的全链路耗时分解。注意 `recall` 是 wrapper phase；`vector_recall` 与 `candidate_hydrate` 是子阶段，不用于推导 total latency，避免双计。

ring buffer 大小可配（默认 1024），暴露 `p50`/`p95`/`p99` 与 `count`/`min`/`max`。`details` 为 per-entity 记录，recorder 聚合为 per-request 的 `detail_fetch` 阶段（取 sum 或 max，由 policy 配置，默认 sum）。`/metrics` 可同时暴露 raw phase 与 canonical phase；canonical 名称必须在 policy 中固定，避免下游 canary 依赖临时字符串。

### 3.4 召回漏斗 ring buffer

每请求记录（ring buffer，默认 512）：`recall_count → hydrated_count → post_filter_count → returned_count` 四元组 + `steps_used` + `oversample_actual`。现有 `QueryDiagnostics` 已提供 `recall_count/post_filter_count/returned_count/steps_used`；实现时从 `RecallResult.fact_map` 得到 `hydrated_count`，从最后一个 `StepDiag.step` 得到 `oversample_actual`。这是 spec §11 的诊断三元组（"记录 recall count、hydrated count、post-filter count"）与 §11 adaptive oversample 步进。为 §11 "强硬过滤召回场景" 五类失败模式与 R7c "空结果率" 门禁供数。

### 3.5 运行时状态快照

每次 `/health` 调用时从 R7a 注入的只读 status seam 读取：当前 `build_id`、`ranking_profile_version`、`generation_profile_version`、`embedding_fingerprint`、`taxonomy_version`、`snapshot_age_s`、`snapshot_stale`（超过 R7a §4 max-stale）、`readiness_last_ok` 时间戳、最近一次 refresh error。来源不是裸 `ActiveSnapshotProvider`：当前协议只有 `get_snapshot()`，不足以表达 readiness 时间线。status seam 可由 live `ReadinessService`/refresh task 更新，**不得**在 health handler 内发起新的 readiness check / snapshot fetch / I/O。

**隐私执行在 recorder，不在 registry**：recorder 接受的是 `RequestDigest`（已脱敏的形状：语言 bucket、query length、filter summary），绝不接受 `query_text`/`embedding`/`contacts`/`student_context` 原始字段。`ai_trace.py` 是唯一允许在显式开关下记录更多的地方，单独处理脱敏（§5）。

## 4. HTTP 面 (ObservabilityServer)

精简 aiohttp app，前缀 `/api/rec`（与 `dext_monitor` 的 `/api/monitor` 区分，二者可共存无路由冲突）。所有路由只读 `GET`，无写。安全模型按挂载方式区分：

- **sidecar 独立监听**：默认绑定 `127.0.0.1:{DEXT_RECOMMEND_OBS_PORT}`；`/health|/metrics|/slo` 可无鉴权。
- **挂到同一个对外 aiohttp app**：必须经过内部鉴权/运维网关，不能依赖 localhost 假设。
- **`/trace`**：无论哪种挂载方式都默认不注册；只有 `DEXT_RECOMMEND_AI_TRACE=1`、配置了 secret、且请求通过内部鉴权时才返回。不得接受 query-string token。

### 4.1 `GET /api/rec/health`

进程存活 + readiness 摘要：

```json
{
  "status": "ok|degraded|unhealthy",
  "uptime_s": 12345.6,
  "snapshot": {
    "build_id": "...", "ranking_profile_version": "...",
    "generation_profile_version": "...", "embedding_fingerprint": "...",
    "taxonomy_version": "...", "age_s": 88.1, "stale": false
  },
  "readiness_last_ok": "2026-07-02T14:03:11Z",
  "dependency": {"qdrant":"ok","neo4j":"ok","embedding":"ok","llm":"ok","catalog":"ok"}
}
```

`degraded` = snapshot stale、最近 refresh 失败或近窗出现 dependency error；`unhealthy` = 无 ACTIVE build 或 readiness 从未成功。R7c §3 "alias/pointer 切换 / catalog 锁 / timeout" 的顶层信号。dependency 状态来自 registry/status seam 的最近观测，不在 handler 中主动探测外部依赖。

### 4.2 `GET /api/rec/metrics`

计数器 + 延迟百分位 + 召回漏斗样本，JSON（Prom 文本为 v1 不做的备选；JSON 对 WebUI/canary 脚本更顺手）：

```json
{
  "counters": { "requests_total": {"new_search|ok": 1203}, ... },
  "latency": {
    "recall":       {"p50":0.012,"p95":0.045,"p99":0.13,"count":1203,"min":0.003,"max":0.21},
    "detail_fetch": {...}
  },
  "recall_funnel": [
    {"recall_count":200,"hydrated_count":80,"post_filter_count":34,
     "returned_count":10,"steps_used":1,"oversample_actual":200}
  ],
  "window": {"since":"2026-07-02T13:00Z","requests":1203}
}
```

### 4.3 `GET /api/rec/slo`

R7c §5 canary 门禁轮询的被动信号：

```json
{
  "window_s": 300,
  "error_rate": 0.012,
  "timeout_rate": 0.003,
  "empty_result_rate": 0.04,
  "underfilled_result_rate": 0.06,
  "permission_denial_rate": 0.02,
  "permission_leakage_rate": 0.0,
  "content_policy_refusal_rate": 0.001,
  "p95_total_latency_s": 1.4,
  "thresholds": {"error_rate":0.05,"timeout_rate":0.02,"empty_result_rate":0.15,
                 "permission_leakage_rate":0.0,"content_policy_refusal_rate":0.01,
                 "p95_total_latency_s":3.0},
  "breaches": []
}
```

`breaches` 列出任何越界指标——非空即 canary controller stop/rollback。阈值存放在 checked-in `data/recommend/observability-policy.json`（与现有 `generation-profile.json` 同约定），不写死 handler。`permission_leakage_rate` 阈值 `0.0` 不可配置——R7c §3 要求泄露必须为 0，任何 cross-owner leakage 或 contacts/review/debug 泄露是硬门禁。正确的 fail-closed `permission_denial_rate` 是信息项，可用于异常流量分析，但不等同于泄露。

### 4.4 `GET /api/rec/trace`

AI trace 采样读取端点，见 §5。要求内部鉴权 + `Authorization: Bearer <trace_secret>`（或等价运维网关注入的可信 principal）。未配置 secret、未开启 trace、缺少鉴权或鉴权失败均返回 404，使 trace 默认不可发现。禁止 `?token=...`，避免 secret 进入 URL、日志、代理和浏览器历史。

**刻意不提供**：`/readiness`（R7a `ActiveSnapshotProvider` 职责，不与 SLO 混）、任何 POST/写端点、跨实例聚合视图。

## 5. 隐私、AI trace 与 Recorder 接口

最 load-bearing 约束。设计在**结构上 + 白名单序列化 + 攻击测试**三层执行 §18 / R7c §3 "不记录 query 原文 / embedding / 联系方式 / API key / source document 全文"。仅靠 dataclass/type hint 不足以构成隐私硬门禁。

### 5.1 Recorder 接口

核心调用的接口只接受脱敏摘要和既有诊断对象，不接受原始 query、embedding、contacts、student_context 或事实全文：

```python
@dataclass(frozen=True, slots=True)
class RequestDigest:
    """脱敏后的单次请求摘要 — 核心被允许传给 monitor 的全部信息。"""
    route_intent: str | None
    language_bucket: str            # "zh"|"en"|"mixed"  (来自 _language_summary)
    query_length: int              # 字符数, 不是原文
    filter_summary: str            # 脱敏摘要：只含字段名/计数/bucket，不含任意用户原文
    used_student_context: bool
    profile_completeness_bucket: str | None   # "low"|"mid"|"high", 非原值
    review_policy: str
    diagnostics_level: str

class Recorder:
    def record_response(
        self, *,
        request_id: str,
        build_id: str | None,
        ranking_profile_version: str | None,
        generation_profile_version: str | None,
        embedding_fingerprint: str | None,
        taxonomy_version: str | None,
        route_intent: str | None,
        outcome: str,                    # ok|warn|error
        total_latency_s: float,
        digest: RequestDigest,
        phase_diagnostics: tuple[PhaseDiagnostic, ...],
        error_code: str | None = None,
        warning_codes: tuple[str, ...] = (),
        recall_count: int | None = None,
        hydrated_count: int | None = None,
        post_filter_count: int | None = None,
        returned_count: int | None = None,
        steps_used: int | None = None,
        oversample_actual: int | None = None,
        content_policy_refusal_op: str | None = None,
        permission_denial_kind: str | None = None,
        permission_leakage_kind: str | None = None,
    ) -> None: ...

    def record_llm_call(self, *, operation: str, outcome: str,
                        latency_s: float | None = None) -> None: ...

    def record_policy_refusal(self, *, operation: str, code: str) -> None: ...
```

**隐私执行要求**：

- `RequestDigest` 无 `query_text`/`embedding`/`contacts`/`student_context`/`entity_id` 字段；调用方只能通过 `RequestDigest.from_request(...)`/等价 factory 构造，factory 内只抽取长度、语言 bucket、filter summary 等安全形状。
- `filter_summary` 不得反射任意用户字符串；若复用现有 `_filter_summary`，需要先改成字段名/数量/bucket 摘要，避免 city/org-unit 等自由文本值被直接写入指标。
- recorder 输出必须走 `to_public_dict()` 白名单序列化，禁止对任意传入对象使用 `dataclasses.asdict()`、`__dict__` 或递归 JSON fallback。
- recorder 是 void 且永不抛异常（内部 try/except 静默吞，monitor 故障绝不能 fail 请求）。
- 隐私攻击测试必须构造带 `query_text`/`embedding`/`contacts`/`student_context` 字段的恶意对象，证明 registry/server/trace 均不会反射这些字段。

### 5.2 核心接入点（5 处，均为既有路径，无新分支）

1. `RecommendationCore.recommend` 所有退出路径 → 记录 `record_response(...)`（在 `ctx.phase_diagnostics` 聚合后调一次，包含 total latency；timeout/error terminal 分支也必须记录）。
2. `ConversationDispatcher.dispatch` 所有退出路径 → 记录 `record_response(...)`，`route_intent` 取 `recommendation/detail_followup/clarification/error` 等安全枚举；detail follow-up 的生成结果不记录原文。
3. `_guarded_async`/`_guarded_sync` 分类错误 → 不单独重复计 request；由 `phase_diagnostics` + terminal `error_code` 统一归因，同时可递增 `external_dependency_errors`。
4. `SafetyGuard.inspect_input` 拒绝 → 记录 `content_policy_refusal`（仅 operation + 分类 code，绝不记 message 原文或被拒输入）。
5. `recall_loop` 返回 → 将 funnel 四元组带入同一次 `record_response`，避免 request 与 funnel 分离导致窗口统计错位。

### 5.3 AI trace (`ai_trace.py`) — 默认关闭，三重门控

- 环境变量 `DEXT_RECOMMEND_AI_TRACE=1` 开启。
- 配置文件或 secret store 必须提供 `trace_secret`；缺失时 `/trace` 不注册或恒 404。
- 请求必须通过内部鉴权（或运维网关注入可信 principal）；鉴权失败返回 404。
- 即使开启，每 N 个请求采样一次（默认 N=100，policy 可配）。
- 开启时，向 trace ring buffer 记录：
  - `RequestDigest` 白名单字段。
  - LLM operation、outcome、latency、warning/error code、token usage（若可得）。
  - evidence_ref 列表截断至 20 条（仅 ref/id/hash，不含内容）。
  - 输出 schema key、JSON parse/schema validation 状态、grounding claim 数量。
- v1 **不记录 query 文本前缀或 LLM output 文本前缀**。英文 query、个人背景和模型输出都可能含 PII；“截断 + 非 ASCII 替换”不是足够脱敏。
- **绝不**：query 原文/前缀、完整 `student_context` 原始字段、embedding 向量、contacts 字段、source document 全文、prompt、API key、被拒违规输入原文、原始 LLM 输出。

### 5.4 policy 文件 `data/recommend/observability-policy.json`

checked-in，与现有 `generation-profile.json` 同约定：

```json
{
  "slo_window_s": 300,
  "thresholds": {
    "error_rate": 0.05,
    "timeout_rate": 0.02,
    "empty_result_rate": 0.15,
    "permission_leakage_rate": 0.0,
    "content_policy_refusal_rate": 0.01,
    "p95_total_latency_s": 3.0
  },
  "ai_trace": {
    "enabled_env": "DEXT_RECOMMEND_AI_TRACE",
    "sample_every_n": 100,
    "text_preview_enabled": false,
    "evidence_ref_limit": 20
  },
  "server": {
    "obs_port_env": "DEXT_RECOMMEND_OBS_PORT",
    "require_internal_auth_when_shared_app": true,
    "allow_query_string_trace_token": false
  }
}
```

policy 未经产品批准不得从 checked-in 默认放宽（R7c §4）。`permission_leakage_rate` 阈值 `0.0` 硬编码，不可配置——泄露是硬禁。允许记录 `permission_denial_rate` 作为信息项，但不得把正确的权限拒绝当作泄露。

### 5.5 与结构化日志的关系

本稿只覆盖 metrics / AI trace 面。结构化应用日志（request ID、build ID、阶段延迟行）是独立关注点，已部分由 `_resilience.py` 分类输出隐含；将日志格式对齐本稿的 digest 形状是小的独立后续，明确不在本稿范围。

## 6. 组合、生命周期与部署

### 6.1 composition root（R7a 拥有，本稿定义 seam）

`build_live_recommendation_runtime(settings)` 返回：

```python
@dataclass(frozen=True, slots=True)
class LiveRecommendRuntime:
    core: RecommendationCore
    dispatcher: ConversationDispatcher
    observability: ObservabilityRuntime   # None under fake/unit-test assembly

@dataclass(frozen=True, slots=True)
class ObservabilityRuntime:
    registry: MetricsRegistry
    recorder: Recorder
    server: ObservabilityServer | None     # None until R7b wires HTTP
```

- 单元测试与 R3–R6 不受影响：`RecommendDeps` 增可选字段 `recorder: Recorder | None = None`（默认 None）。core 在每个 call site 单次 `if self._deps.recorder is not None: ...` None-check，缺席时零行为变化。`ports/_fakes.py` 无需改动。
- `MetricsRegistry` 构造一次共享；`Recorder` 持其引用。两者都可用 fake/no-op snapshot 在进程内测试中构造。
- R7a 还需注入只读 `RuntimeStatusProvider`（名称可按实现调整）：暴露当前 snapshot 元数据、`readiness_last_ok`、最近 refresh error、stale 判定参数；`observability` handler 不直接调用 readiness check。

### 6.2 生命周期（镜像 R7a §3 shutdown 逆序）

- 启动：registry 构造 → recorder 包裹 → 附到 `RecommendDeps` → core 装配。`ObservabilityServer` 此处**不**启动（R7a 无 HTTP）。
- R7b：HTTP app 构建时，`/api/rec/*` 路由挂到同一 aiohttp app（需内部鉴权）或 `DEXT_RECOMMEND_OBS_PORT` 上的 localhost sidecar。registry 作为 app key 传递，同 `dext_monitor` 的 `SERVICE_KEY` 模式。
- 关闭：逆序——`ObservabilityServer` 先停，再关 core 的 pools。registry 无资源需关（纯内存）。

### 6.3 部署绑定

`ObservabilityServer` 默认 `127.0.0.1:{DEXT_RECOMMEND_OBS_PORT}` 仅 localhost。监控数据虽脱敏，不面向公网；远程观测需经运维侧反向代理 + 鉴权层，不在本稿范围。若与产品 API 共用对外 app，则必须显式接入内部鉴权中间件；否则不得注册 observability routes。

## 7. 测试策略（TDD，遵循项目测试政策）

- **`metrics.py`**：计数器递增单测（`asyncio.to_thread` 下并发写）、RequestEvent rolling window 裁剪、ring buffer 百分位计算（对已知序列验证 p50/p95/p99 正确性）、窗口轮转。
- **`recorder.py`**：每个 `record_response`/`record_llm_call`/`record_policy_refusal` 不抛异常（喂坏输入，断言无异常）；`RequestDigest` factory 只抽取白名单字段；`to_public_dict()` 不反射 schema 外字段。
- **`server.py`**：`aiohttp.test_utils.TestServer`/`TestClient`（项目既有 SP4 模式，无需 `pytest-aiohttp`）。断言 `/health`、`/metrics`、`/slo` 形状；断言 `/slo.breaches` 在 rolling window 指标越阈值时填充；断言 sidecar localhost-only 配置与 shared-app internal-auth 配置不能同时缺失。
- **`ai_trace.py`**：开关关闭时 trace ring buffer 即使有请求也保持空；开启但无 secret/无内部鉴权时 `/trace` 返回 404；开启且通过鉴权时只返回 digest、operation、code、ref/hash、usage 等白名单字段，不包含 query/LLM output 文本前缀。
- **隐私攻击测试**（项目 R0/R1 acceptance bar memory 要求 "attack tests on final output" 强制）：喂 recorder 含 `query_text`/`embedding`/`contacts`/`student_context` 的恶意对象，断言 `/metrics`、`/slo`、`/trace` 输出中均不出现这些字段和值。这是硬门禁。
- **无 live LLM**：observability 测试喂合成 digest，不触真 LLM（遵循 SP5+ LLM-key 规则；observability 是 LLM-free 的）。

## 8. 验收门禁

1. `dext_recommend/observability/` 包存在，`__all__` 清晰；import-boundary 测试确认不导入 `dext`/`dext_graph`/`dext_monitor`。
2. `RecommendDeps.recorder` 可选（None 默认）；既有 R3–R5 测试不变通过（不接 recorder）。
3. `/api/rec/health|/metrics|/slo` 在 `TestClient` 下返回规定形状；`/slo.breaches` 在 rolling-window 阈值越界时非空。
4. 隐私攻击测试通过：无论 AI trace 开关，`/metrics`、`/slo`、`/trace` 输出均无 query 原文/前缀、LLM output 原文/前缀、embedding、contacts、student_context 原始字段和值。
5. AI-trace 端点无 secret、无内部鉴权或未开启时返回 404；禁止 query-string token；开启时只记白名单字段。
6. SLO 阈值从 `data/recommend/observability-policy.json` 加载；`permission_leakage_rate` 阈值硬编码 `0.0`（不可经 policy 覆盖），`permission_denial_rate` 仅为信息项。
7. `/health` 使用 R7a 注入的 runtime status seam；handler 不直接触发 readiness check 或外部 I/O。
8. 阶段名测试覆盖现有 raw phase（`vector_recall`/`candidate_hydrate`/`details`）与 canonical phase mapping，防止下游依赖不稳定字符串。

## 9. 与现有 specs 的契约对齐

| spec 要求 | 本稿落地 |
|---|---|
| overview §18 记录 request ID / build ID / ranking profile version / query 长度·语言摘要 / 过滤摘要 / recall·post-filter·returned count / warning·error·耗时分解 | `RequestDigest` + `RequestEvent` rolling window + `MetricsRegistry` 计数器 + 阶段延迟 ring buffer |
| overview §18 "不得记录 API key / 完整 embedding / 未脱敏联系方式 / 可识别身份长 query / 档案原文 / source document 全文" | `RequestDigest` + 白名单序列化强制无这些字段；AI trace 三重门控且 v1 无文本预览 |
| overview §18 "AI trace 仅在显式开关下采样记录并脱敏截断" | `ai_trace.py` `DEXT_RECOMMEND_AI_TRACE` + sample_every_n + secret + internal auth；v1 不记录 query/LLM output 文本前缀 |
| R7a §4 "记录 build/profile/fingerprint、phase latency、错误码与 pool saturation；禁止记录 query 原文、embedding、联系人或 API key" | `/health` status seam 元数据 + 阶段延迟 + `external_dependency_errors` + 可选 `pool_saturation_samples` |
| R7c §3 "alias/pointer 切换 / catalog 锁 / timeout / 权限硬门禁为 0" | `/health` status + `permission_leakage_total` + `permission_leakage_rate=0.0` 硬门禁；正确权限拒绝另记为信息项 |
| R7c §5 "rollout 期间 build pointer、错误率、超时、空结果率、权限拒绝率或质量指标越界立即停止并回滚" | `/slo` 被动信号面 + `breaches` 列表，供 canary controller 轮询 |

## 10. 落地步骤建议

本稿为独立 spec；实现计划由后续 `writing-plans` 产出。建议任务切片（供 plan 参考）：

1. `observability/` 骨架 + `settings.py` + import-boundary 测试。
2. `metrics.py` 计数器 + RequestEvent rolling window + ring buffer + 百分位 + 单测。
3. `recorder.py` `RequestDigest` + `Recorder.record_response`/`record_llm_call` no-throw + 白名单序列化 + 隐私攻击测试。
4. 5 处核心接入点接线（`RecommendDeps.recorder` 可选，None 短路）。
5. `server.py` `/health` `/metrics` `/slo` + `TestClient` 测试 + policy 加载 + sidecar/shared-app 安全配置测试。
6. `ai_trace.py` 三重门控采样 + 无文本预览白名单输出 + `/trace` header/secret/internal-auth 端点 + 攻击测试。
7. R7a composition root 装配 `ObservabilityRuntime`（`server=None` 直到 R7b）。

每步 TDD：RED → 最小实现 → GREEN → 一个 conventional commit。
