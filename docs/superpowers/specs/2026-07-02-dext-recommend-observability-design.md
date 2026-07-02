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

为 `dext_recommend` 补齐**服务端运行时可观测性**：进程内指标计数、阶段延迟分解、召回漏斗、SLO 信号面与受控 AI trace 采样。填补 specs §18（隐私与日志）、R7a §4（韧性与观测）、R7c §3–§5（E2E/故障注入/发布门禁）要求但当前无人实现的缺口。

本稿严格服从以下边界（来自 overview §3、§6.3、§7）：

- `dext_recommend/observability/` 只从 `dext_recommend` 内部模块 import，**不 import** `dext`、`dext_graph`、`dext_monitor`。
- 不写 catalog SQLite、Qdrant、Neo4j、PostgreSQL；只读自身进程内 `MetricsRegistry`。
- aiohttp 不进入 `core/`；HTTP 面放在 `observability/server.py`，由 R7a composition root 装配。
- monitor 是**被动信号面**：只暴露 `/health`、`/metrics`、`/slo`、`/trace`，不触发回滚，不参与发布决策（R7c canary controller 消费 `/slo` 后自行决定）。
- `/readiness` 不归本稿：仍是 R7a `ActiveSnapshotProvider` 的 ACTIVE build 一致性职责，不混入 SLO。

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
  settings.py            # ObservabilitySettings dataclass (挂在 RecommendSettings 下)
  metrics.py             # MetricsRegistry: 原子计数器 + 阶段延迟 ring buffer + 召回漏斗
  recorder.py            # Recorder: 核心调用的 void no-throw 接口 + RequestDigest
  ai_trace.py            # DEXT_REC_AI_TRACE 双门控采样器 (脱敏 + 截断)
  server.py              # ObservabilityServer: aiohttp 只读 GET, 挂 /api/rec/*
```

**三个角色，单向数据流：**

1. **`MetricsRegistry`** — runtime composition root 创建的单例，持有原子计数器与 ring buffer。唯一 mutable state。
2. **`Recorder`** — 核心调用的 void 接口 `record_request(phase, latency_s, digest, ...)`，注入 `RecommendDeps`。永不抛异常、永不阻塞请求路径（写入为 in-process 原子操作）。测试中可为 `None`/fake。
3. **`ObservabilityServer`** — 只读 aiohttp app，从 registry 读取并暴露 `/api/rec/*`。仅在 R7b/R7c 接线（HTTP 层归 R7b）；registry 与 recorder 从 R3 起即存在，HTTP 之前指标也可在进程内测试中累积。

**装配**：`build_live_recommendation_runtime(settings)`（R7a composition root）构造 `MetricsRegistry`、包成 `Recorder`、附到 `RecommendDeps`，并在 R7b 后可选地启动 `ObservabilityServer`。fake ports 单元测试不变（recorder 默认 `None`，core 用 `if recorder is not None` 短路）。

**边界合规**：`observability/` 不导入 `dext_monitor`，不跨包共享内部类。它读取的唯一外部事实是自身进程内 registry 与附加在 deps 上的 `ActiveSnapshotProvider` 句柄（仅读当前 snapshot 元数据，不发起新的 snapshot fetch / I/O）。

## 3. 指标注册表 (MetricsRegistry)

单进程事件循环内访问；计数器用单个 `threading.Lock`（适配 `asyncio.to_thread` 写入，KISS，不用无锁原语）。

### 3.1 请求级计数器（始终开启，summary 级）

| 计数器 | 维度 | 来源 |
|---|---|---|
| `requests_total` | `route_intent`, `outcome`(`ok\|warn\|error`) | recommend/dispatcher 入口/出口 |
| `errors_total` | `error_code`(`RecommendationErrorCode`) | `ClassifiedRecommendError`、terminal 分支 |
| `warnings_total` | `warning_code` | `RecommendationWarning.code` |
| `content_policy_refusals_total` | `operation`(`recommend\|conversation\|detail\|match\|email\|compare`) | `SafetyGuard` 命中 |
| `permission_denials_total` | `kind`(`contacts\|review\|debug`) | `UNAUTHORIZED_*` 分支 |
| `empty_results_total` | — | `NO_CANDIDATES_AFTER_FILTERS`、`returned < limit` |
| `timeout_total` | `phase` | `asyncio.TimeoutError` 分支 |
| `llm_calls_total` | `operation`, `outcome` | query-understanding/intent/generation |
| `external_dependency_errors` | `dep`(`qdrant\|neo4j\|embedding\|llm\|catalog`) | `_guarded_*` 分类 |

### 3.2 阶段延迟 ring buffer

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
| `details` | `detail_fetch.py` 的 `ctx.record`（per-entity，非 per-request） |

`recall`、`rerank`、`cards`、`validation` 当前**未**经 `ctx.record` 发出耗时。本稿落地时 recorder 先消费上述已存在的 phase tuple；补齐 `recall`/`rerank`/`cards`/`validation` 的 `ctx.record` 是本稿的一项**实现任务**（在既有路径上各加一次 `ctx.record`，不引入新分支），以便 `/metrics` 覆盖 spec §18 要求的全链路耗时分解。补齐后阶段集合为 11 个：上述 7 个 + `recall` + `rerank` + `cards` + `validation`。

ring buffer 大小可配（默认 1024），暴露 `p50`/`p95`/`p99` 与 `count`/`min`/`max`。`details` 为 per-entity 记录，recorder 聚合为 per-request 的 `detail_fetch` 阶段（取 sum 或 max，由 policy 配置，默认 sum）。

### 3.3 召回漏斗 ring buffer

每请求记录（ring buffer，默认 512）：`recall_count → post_filter_count → returned_count` 三元组 + `steps_used` + `oversample_actual`。这是 spec §11 的诊断三元组（"记录 recall count、hydrated count、post-filter count"）与 §11 adaptive oversample 步进。为 §11 "强硬过滤召回场景" 五类失败模式与 R7c "空结果率" 门禁供数。

### 3.4 运行时状态快照

每次 `/health` 调用时重新计算：当前 `build_id`、`ranking_profile_version`、`generation_profile_version`、`embedding_fingerprint`、`taxonomy_version`、`snapshot_age_s`、`snapshot_stale`（超过 R7a §4 max-stale）、`readiness_last_ok` 时间戳。来源是附加在 deps 上的 `ActiveSnapshotProvider` 句柄，**不**发起额外 snapshot fetch（无额外 I/O）。

**隐私执行在 recorder，不在 registry**：recorder 接受的是 `RequestDigest`（已脱敏的形状：语言 bucket、query length、filter summary），绝不接受 `query_text`/`embedding`/`contacts`/`student_context` 原始字段。`ai_trace.py` 是唯一允许在显式开关下记录更多的地方，单独处理脱敏（§5）。

## 4. HTTP 面 (ObservabilityServer)

精简 aiohttp app，前缀 `/api/rec`（与 `dext_monitor` 的 `/api/monitor` 区分，二者可共存无路由冲突）。所有路由只读 `GET`，无写、无鉴权（仅 localhost 绑定，见 §6）。默认绑定 `127.0.0.1:{DEXT_REC_OBS_PORT}`——监控数据虽已脱敏，不面向公网。

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

`degraded` = snapshot stale 或近窗出现 dependency error；`unhealthy` = 无 ACTIVE build 或 readiness 从未成功。R7c §3 "alias/pointer 切换 / catalog 锁 / timeout" 的顶层信号。

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
    {"recall_count":200,"post_filter_count":34,"returned_count":10,"steps_used":1}
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
  "permission_deny_rate": 0.0,
  "content_policy_refusal_rate": 0.001,
  "p95_total_latency_s": 1.4,
  "thresholds": {"error_rate":0.05,"timeout_rate":0.02,"empty_result_rate":0.15,
                 "permission_deny_rate":0.0,"content_policy_refusal_rate":0.01,
                 "p95_total_latency_s":3.0},
  "breaches": []
}
```

`breaches` 列出任何越界指标——非空即 canary controller stop/rollback。阈值存放在 checked-in `data/recommend/observability-policy.json`（与现有 `generation-profile.json` 同约定），不写死 handler。`permission_deny_rate` 阈值 `0.0` 不可配置——R7c §3 要求泄露必须为 0，任何 cross-owner leakage 或 contacts 泄露是硬门禁。

### 4.4 `GET /api/rec/trace?token=...`

AI trace 采样读取端点，见 §5。无 `trace_token` 返回 404，使 trace 默认不可发现。

**刻意不提供**：`/readiness`（R7a `ActiveSnapshotProvider` 职责，不与 SLO 混）、任何 POST/写端点、跨实例聚合视图。

## 5. 隐私、AI trace 与 Recorder 接口

最 load-bearing 约束。设计在**结构上**而非仅靠约定执行 §18 / R7c §3 "不记录 query 原文 / embedding / 联系方式 / API key / source document 全文"。

### 5.1 Recorder 接口

核心调用的接口，仅此：

```python
@dataclass(frozen=True, slots=True)
class RequestDigest:
    """脱敏后的单次请求摘要 — 核心被允许传给 monitor 的全部信息。"""
    route_intent: str | None
    language_bucket: str            # "zh"|"en"|"mixed"  (来自 _language_summary)
    query_length: int              # 字符数, 不是原文
    filter_summary: str            # 来自 _filter_summary 的脱敏摘要
    used_student_context: bool
    profile_completeness_bucket: str | None   # "low"|"mid"|"high", 非原值
    review_policy: str
    diagnostics_level: str

class Recorder:
    def record_request(self, *, phase: str, latency_s: float,
                        digest: RequestDigest,
                        error_code: str | None = None,
                        warning_codes: tuple[str, ...] = (),
                        recall_count: int | None = None,
                        post_filter_count: int | None = None,
                        returned_count: int | None = None,
                        steps_used: int | None = None,
                        content_policy_refusal_op: str | None = None,
                        permission_denial_kind: str | None = None) -> None: ...
```

**类型层隐私强制**：`RequestDigest` 无 `query_text`/`embedding`/`contacts`/`student_context`/`entity_id` 字段——通过此接口无法泄漏原始 query。recorder 是 void 且永不抛异常（内部 try/except 静默吞，monitor 故障绝不能 fail 请求）。

### 5.2 核心接入点（5 处，均为既有路径，无新分支）

1. `RecommendationCore.recommend` 退出 → 记录 request 结果 + digest（在 `ctx.phase_diagnostics` 聚合后调一次）。
2. `ConversationDispatcher.dispatch` 退出 → 记录 `route_intent=detail_followup` + generation 结果。
3. `_guarded_async`/`_guarded_sync` 分类错误 → 记录 dependency error（已在 `ClassifiedRecommendError` 分类）。
4. `SafetyGuard.inspect_input` 拒绝 → 记录 `content_policy_refusal`（仅 operation + 分类 code，绝不记 message 原文）。
5. `recall_loop` 返回 → 记录 funnel 三元组。

### 5.3 AI trace (`ai_trace.py`) — 默认关闭，双重门控

- 环境变量 `DEXT_REC_AI_TRACE=1` 开启。
- 即使开启，每 N 个请求采样一次（默认 N=100，policy 可配）。
- 开启时，除 digest 外，向 trace ring buffer（仅经 `/api/rec/trace?token=...` 访问）记录：
  - query 头部前 80 字符，非 ASCII 替换为 `?`（脱敏）。
  - LLM operation + evidence_ref 列表截断至 20 条（仅 ref，不含内容）。
  - LLM output 头部前 200 字符，PII 剥离（email/phone 正则）。
- **绝不**：完整 `student_context` 原始字段、embedding 向量、contacts 字段、source document 全文、API key、被拒违规输入原文。
- trace 端点要求配置文件中的 `trace_token`；缺失返回 404，使 trace 默认不可发现。

### 5.4 policy 文件 `data/recommend/observability-policy.json`

checked-in，与现有 `generation-profile.json` 同约定：

```json
{
  "slo_window_s": 300,
  "thresholds": {
    "error_rate": 0.05,
    "timeout_rate": 0.02,
    "empty_result_rate": 0.15,
    "permission_deny_rate": 0.0,
    "content_policy_refusal_rate": 0.01,
    "p95_total_latency_s": 3.0
  },
  "ai_trace": {
    "enabled_env": "DEXT_REC_AI_TRACE",
    "sample_every_n": 100,
    "query_head_chars": 80,
    "llm_output_head_chars": 200
  }
}
```

policy 未经产品批准不得从 checked-in 默认放宽（R7c §4）。`permission_deny_rate` 阈值 `0.0` 硬编码，不可配置——泄露是硬禁。

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

### 6.2 生命周期（镜像 R7a §3 shutdown 逆序）

- 启动：registry 构造 → recorder 包裹 → 附到 `RecommendDeps` → core 装配。`ObservabilityServer` 此处**不**启动（R7a 无 HTTP）。
- R7b：HTTP app 构建时，`/api/rec/*` 路由挂到同一 aiohttp app（或 `DEXT_REC_OBS_PORT` 上的 sidecar）。registry 作为 app key 传递，同 `dext_monitor` 的 `SERVICE_KEY` 模式。
- 关闭：逆序——`ObservabilityServer` 先停，再关 core 的 pools。registry 无资源需关（纯内存）。

### 6.3 部署绑定

`ObservabilityServer` 默认 `127.0.0.1:{DEXT_REC_OBS_PORT}` 仅 localhost。监控数据虽脱敏，不面向公网；远程观测需经运维侧反向代理 + 鉴权层，不在本稿范围。

## 7. 测试策略（TDD，遵循项目测试政策）

- **`metrics.py`**：计数器递增单测（`asyncio.to_thread` 下并发写）、ring buffer 百分位计算（对已知序列验证 p50/p95/p99 正确性）、窗口轮转。
- **`recorder.py`**：每个 `record_request` 不抛异常（喂坏输入，断言无异常）；`RequestDigest` 构造拒绝 schema 外字段（dataclass + 边界类型检查）。
- **`server.py`**：`aiohttp.test_utils.TestServer`/`TestClient`（项目既有 SP4 模式，无需 `pytest-aiohttp`）。断言 `/health`、`/metrics`、`/slo` 形状；断言 `/slo.breaches` 在计数器越阈值时填充；断言 localhost-only 绑定。
- **`ai_trace.py`**：开关关闭时 trace ring buffer 即使有请求也保持空；开启时断言 PII 脱敏（email/phone 正则）、head-char 截断、`trace_token` 门控（无 token 返回 404）。
- **隐私攻击测试**（项目 R0/R1 acceptance bar memory 要求 "attack tests on final output" 强制）：喂 recorder 含 `query_text`/`embedding`/`contacts` 的对象，断言 `/metrics`、`/slo`、`/trace` 输出中均不出现这些字段。这是硬门禁。
- **无 live LLM**：observability 测试喂合成 digest，不触真 LLM（遵循 SP5+ LLM-key 规则；observability 是 LLM-free 的）。

## 8. 验收门禁

1. `dext_recommend/observability/` 包存在，`__all__` 清晰；import-boundary 测试确认不导入 `dext`/`dext_graph`/`dext_monitor`。
2. `RecommendDeps.recorder` 可选（None 默认）；既有 R3–R5 测试不变通过（不接 recorder）。
3. `/api/rec/health|/metrics|/slo` 在 `TestClient` 下返回规定形状；`/slo.breaches` 在阈值越界时非空。
4. 隐私攻击测试通过：无论 AI trace 开关，`/metrics`、`/slo`、`/trace` 输出均无 query 原文 / embedding / contacts / student_context 原始字段。
5. AI-trace 端点无 `trace_token` 返回 404；开启时只记脱敏 + 截断字段。
6. SLO 阈值从 `data/recommend/observability-policy.json` 加载；`permission_deny_rate` 阈值硬编码 `0.0`（不可经 policy 覆盖）。

## 9. 与现有 specs 的契约对齐

| spec 要求 | 本稿落地 |
|---|---|
| overview §18 记录 request ID / build ID / ranking profile version / query 长度·语言摘要 / 过滤摘要 / recall·post-filter·returned count / warning·error·耗时分解 | `RequestDigest` + `MetricsRegistry` 计数器 + 阶段延迟 ring buffer |
| overview §18 "不得记录 API key / 完整 embedding / 未脱敏联系方式 / 可识别身份长 query / 档案原文 / source document 全文" | `RequestDigest` 类型层强制无这些字段；AI trace 双门控 + 脱敏截断 |
| overview §18 "AI trace 仅在显式开关下采样记录并脱敏截断" | `ai_trace.py` `DEXT_REC_AI_TRACE` + sample_every_n + token 门控 |
| R7a §4 "记录 build/profile/fingerprint、phase latency、错误码与 pool saturation；禁止记录 query 原文、embedding、联系人或 API key" | `/health` 快照元数据 + 阶段延迟 + `external_dependency_errors` |
| R7c §3 "alias/pointer 切换 / catalog 锁 / timeout / 权限硬门禁为 0" | `/health` status + `permission_denials_total` + `permission_deny_rate=0.0` 硬门禁 |
| R7c §5 "rollout 期间 build pointer、错误率、超时、空结果率、权限拒绝率或质量指标越界立即停止并回滚" | `/slo` 被动信号面 + `breaches` 列表，供 canary controller 轮询 |

## 10. 落地步骤建议

本稿为独立 spec；实现计划由后续 `writing-plans` 产出。建议任务切片（供 plan 参考）：

1. `observability/` 骨架 + `settings.py` + import-boundary 测试。
2. `metrics.py` 计数器 + ring buffer + 百分位 + 单测。
3. `recorder.py` `RequestDigest` + `Recorder.record_request` no-throw + 隐私攻击测试。
4. 5 处核心接入点接线（`RecommendDeps.recorder` 可选，None 短路）。
5. `server.py` `/health` `/metrics` `/slo` + `TestClient` 测试 + policy 加载。
6. `ai_trace.py` 双门控采样 + 脱敏 + `/trace` token 端点 + 攻击测试。
7. R7a composition root 装配 `ObservabilityRuntime`（`server=None` 直到 R7b）。

每步 TDD：RED → 最小实现 → GREEN → 一个 conventional commit。
