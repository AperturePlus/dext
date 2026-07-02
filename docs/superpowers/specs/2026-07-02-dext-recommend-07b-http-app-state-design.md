# 阶段 7b：dext_recommend HTTP/OpenAPI 与 application state

> 状态：详细设计，可进入实现
>
> 前置依赖：[R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md) 已具备可组合、可关闭、测试通过的 live root
>
> 契约源：`docs/appside/openapi.yaml`；本规格发现的契约缺口必须先在 OpenAPI 中修正，再写 handler
>
> 内容安全：HTTP/application 层只调用共享 admission/safety 能力并映射结构化结果，不复制规则、正则或 prompt
>
> 后续阶段：[R7c production acceptance](2026-07-02-dext-recommend-07c-production-acceptance-design.md)

## 1. 目标、范围与退出边界

R7b 把 R7a 的进程内 runtime 接成一个 `aiohttp` application，并实现推荐域所需的 PostgreSQL
application state。完成后应能在单个服务进程中提供鉴权、推荐/教授/对话 HTTP API、用户 profile、
favorites、history、匿名身份与远端资料删除。

R7b 负责：

- `/api/v1` application factory、middleware、路由、DTO 和 OpenAPI contract tests；
- `LiveRecommendationRuntime` 与 SQLAlchemy async engine 的 startup/shutdown 接线；
- fail-closed identity、权限和 owner scope；
- PostgreSQL migration、repository、事务、幂等、乐观并发和远端资料清理；
- conversation session/turn/fork/attempt/message 的 application orchestration；
- SSE 生命周期、断连取消和 attempt 状态收敛；
- 内部 immutable model 与公开 OpenAPI DTO 的显式映射；
- 日志、响应和持久化的隐私边界。

R7b 不负责：

- competition、preparation plan、home/config/模板等其他领域路径；
- catalog、Neo4j、Qdrant 或 competition knowledge base 的写入/删除；
- 新的排序、过滤、证据、内容政策、prompt 或生成业务逻辑；
- production rollout、容量、故障演练和真实依赖验收（归 R7c）；
- 多进程/多节点 attempt cancellation；R7b 验收拓扑为单 `aiohttp` 进程；
- 当前 core 尚无能力的 `/profile/achievements/extract`、`/chat/route`、
  `/chat/quick-actions`。这三条路径在补齐独立、可测试的 core service 前不得在 handler 中临时实现。

最后三条仍保留在 OpenAPI 中，但 R7b contract manifest 必须明确标为 `deferred`；不得返回假成功、
静态占位内容或在 route 中直接调用 LLM。若产品要求把它们纳入 R7b，则先增加 R5/R6 follow-up core
spec 和 service，再更新本规格的 owned manifest。

R7b 完成只代表服务功能可用，不代表允许 production rollout。

## 2. 实现前必须闭合的 OpenAPI 缺口

`docs/appside/openapi.yaml` 是唯一公开契约。实现的第一个提交只做契约闭合与 contract fixture，
不得同时写业务 handler。

### 2.1 owned-path manifest

新增 checked-in manifest（建议 `tests/dext_recommend/api/owned_paths.yaml`），把路径分为：

1. `implemented`：本阶段必须通过 contract test；
2. `deferred`：上述三个缺 core service 的路径；
3. `external`：competition/preparation/home 等其他模块路径。

contract test 必须断言 OpenAPI 中每条路径只属于一个集合，并阻止新增路径被默认为已实现。

### 2.2 必改契约项

- 为 owned operation 补稳定 `operationId`；名称一经发布不得复用或漂移。
- 定义统一 `ErrorEnvelope`，至少含整数 `code`、稳定字符串 `error_code`、安全 `message`、
  `request_id` 与 `data: null`；为 owned operation 补 400/401/403/404/409/422/429/500/503/504
  中实际可能出现的响应。
- 所有 owned request schema 显式 `additionalProperties: false`，与 Pydantic `extra="forbid"`
  一致；不得出现“OpenAPI 接受、服务拒绝”的双重契约。
- `AcademicScore` 当前公开 raw `gpa/scale/rank`，不能直接进入 `StudentContext`。
  R7b 将契约改为 `gpa_bucket` / `rank_bucket`，枚举与
  `dext_grounded/rules/grounded_v1.yaml` 一致；不定义临时分桶阈值，不把 raw 数值写入日志或
  recommendation history。若 UI 仍需 raw 值，应放在另一个明确隔离且不进入生成链路的产品规格。
- cookie-auth 的 unsafe operation 增加 `X-CSRF-Token` header 契约；bearer-auth 不要求该 header。
- `Idempotency-Key` 的格式、最大长度（128）、作用域和冲突响应写入 OpenAPI；body `request_id`
  仍保留，两者必须一致绑定，不能互相替代。
- SSE operation 补齐 event schema 与示例；不能只声明 `type: string`。
- `/professors/{professor_id}` 继续公开匿名访问，但响应永不包含 contacts/review-only 字段。

### 2.3 DTO 规则

- Pydantic v2 DTO 只描述公开契约，禁止直接 `model_dump()` internal dataclass。
- request DTO 使用 strict 类型、长度/数量上限、UUID/date-time 校验和 `extra="forbid"`。
- response adapter 逐字段构造 DTO；内部 `phase_diagnostics`、claims、fact refs、risk flags、
  generation manifest 等未公开字段默认丢弃。
- debug 字段只有在 OpenAPI 允许、principal 有 `diagnostics` 权限且服务端开关开启时才可返回；
  三者缺一则不返回，而不是返回空壳字段。
- 所有成功 envelope 固定 `code=0, message="ok"`；错误 envelope 与 HTTP status 同时表达失败，
  不使用 HTTP 200 包装 error。

## 3. 模块布局与依赖边界

建议布局：

```text
src/dext_recommend/
  api/
    app.py                 # aiohttp factory + cleanup_ctx
    keys.py                # typed AppKey
    middleware.py          # request-id/error/access-log/body-limit
    auth.py                # Principal / IdentityProviderPort / CSRF
    schemas.py             # Pydantic OpenAPI DTO
    adapters.py            # DTO <-> internal model
    error_mapping.py       # domain/application error -> HTTP
    routes/
      recommendations.py
      professors.py
      conversations.py
      profile.py
      favorites.py
      history.py
      identity.py
  application/
    conversations.py       # transaction + dispatch + persistence orchestration
    identities.py
    profile.py
    favorites.py
    history.py
    idempotency.py
    sse.py
  app_state/
    db.py                  # async engine/sessionmaker/version check
    models.py              # SQLAlchemy mappings
    repositories.py        # owner-scoped repositories
    conversation_store.py  # ConversationStorePort implementation
    migrations/            # Alembic env + revisions
```

依赖方向固定为：

```text
routes -> application -> core ports/services
                    \-> app_state repositories
schemas <-> adapters
app factory -> runtime + repositories + routes
```

禁止项：

- `core`、`generation`、`ports` import `aiohttp` / SQLAlchemy / asyncpg；
- `api` 或 `app_state` import `dext_graph`；
- repository 调用 recommendation core；
- handler 拼 prompt、做排序/过滤、读取 raw catalog SQL 或直接调用 OpenAI/Qdrant/Neo4j；
- 以 module global 保存 engine、session、principal、request 或运行中的 task。

## 4. 配置、依赖与 application lifecycle

### 4.1 依赖与配置

新增运行依赖：

- `asyncpg`；
- `alembic`；
- 继续使用现有 SQLAlchemy 2、aiohttp、Pydantic v2。

新增 `AppSettings`（前缀 `DEXT_APP_`），至少包含：

```text
database_url
database_pool_size / database_max_overflow / database_pool_timeout
database_statement_timeout
http_host / http_port / request_body_max_bytes
shutdown_grace_seconds
anonymous_token_ttl_seconds
auth_token_pepper                # SecretStr
cookie_secure / cookie_domain / cookie_samesite
csrf_allowed_origins
diagnostics_enabled
sse_heartbeat_seconds
idempotency_ttl_seconds
```

`database_url` 接受现有 `.env.example` 的 `postgresql://...`，内部明确转换为
`postgresql+asyncpg://...`；日志和异常不得输出含密码 DSN。生产 `cookie_secure` 必须为 true，
localhost 测试可显式覆盖。

### 4.2 migration 策略

- Alembic revision 是唯一 schema 变更入口；禁止 application startup 调 `metadata.create_all()`。
- startup 只检查当前 revision 是否为 head；落后、超前或无法读取均 fail-fast。
- 本地/部署前显式执行 `alembic upgrade head`。
- migration downgrade 只用于开发验证；远端资料清理不能依赖 downgrade。

### 4.3 startup/shutdown 顺序

`create_recommendation_app(settings, *, runtime_factory=..., identity_provider=...)` 返回尚未启动的
`web.Application`。`cleanup_ctx` 执行：

1. 校验 AppSettings、cookie/CSRF 安全组合和 migration head；
2. 创建 async engine，执行 `SELECT 1` 与 statement timeout readback；
3. 创建 owner-scoped repositories 与 `PostgresConversationStore`；
4. 调用扩展后的 `build_live_recommendation_runtime(..., conversation_store=store)`；
5. 创建 application services、attempt task registry，写入 typed `AppKey`；
6. 开始接收请求。

shutdown 逆序：停止接收请求 → cancel/await 所有 running attempt（限 grace）→
`runtime.aclose()` → `engine.dispose()`。每个 close 隔离错误但继续后续清理；第二次 cleanup no-op。

R7b 只允许给 R7a composition root 增加显式 `conversation_store` injection seam；不得构造 runtime 后
修改 frozen `RecommendDeps` 或 monkeypatch `core.deps`。

## 5. 身份、权限、CSRF 与 owner scope

### 5.1 Principal

```python
@dataclass(frozen=True, slots=True)
class Principal:
    owner_id: UUID
    auth_kind: Literal["anonymous", "bearer", "cookie"]
    include_contacts: bool = False
    can_view_review: bool = False
    diagnostics: bool = False
```

`ViewerPermissions` 只能由可信 Principal 派生。request body/query 中出现同名字段不得提升权限。

### 5.2 anonymous identity

`POST /identity/anonymous`：

- 生成 UUID owner 与至少 256-bit 随机 opaque token；
- PostgreSQL 只保存 `HMAC-SHA256(auth_token_pepper, token)`，不保存明文；
- response body 仅本次返回 `access_token`；同时设置 `scho_anonymous` HttpOnly cookie；
- 另设非 HttpOnly `scho_csrf` random cookie，unsafe cookie-auth request 必须通过 double-submit 校验；
- cookie 使用 `Path=/api/v1`、配置化 Domain、`SameSite=Lax`、生产 `Secure`、明确 Max-Age；
- identity 创建受 IP/设备级轻量限流，但限流 key 不写入业务表。

### 5.3 token 解析

- protected route 缺 credential → 401；无 provider、无效、过期、撤销 → 401；
- bearer 与 cookie 同时存在时必须解析为同一 owner，否则 401；
- unsafe cookie-auth 缺/错 CSRF token 或 Origin 不在 allowlist → 403；
- path/body 中的 owner/session/professor ID 永远不能替代 principal；
- repository 方法第一个参数固定为 `owner_id`，查询必须在 SQL 层带 owner predicate；
- 跨 owner 的资源统一表现为 404，避免枚举；权限不足但资源可公开识别时用 403。

## 6. PostgreSQL schema 与不变量

所有时间为 `TIMESTAMPTZ` UTC，JSON 使用 `JSONB`，ID 使用 UUID；所有可变表包含 `created_at`、
`updated_at`。用户内容列不得进入普通索引或日志。

### 6.1 表

| 表 | 关键列/约束 |
|---|---|
| `app_identities` | `owner_id PK`、`kind`、`token_digest UNIQUE`、`expires_at`、`revoked_at` |
| `app_profiles` | `owner_id PK`、`profile_json`、`revision >= 0` |
| `conversation_sessions` | composite PK `(owner_id,id)`；`kind`、`root_session_id`、`source_session_id`、`source_turn_id`、`professor_id`、`revision`、`deleted_at` |
| `conversation_turns` | composite PK `(owner_id,id)`；composite FK session；`ordinal`、`status`、`route`、`user_message_id`、`active_attempt_id`、`context_json`、version pins |
| `conversation_attempts` | composite PK `(owner_id,id)`；FK turn；`request_id`、`idempotency_key`、`request_hash`、`status`、`worker_id`、`cancel_requested_at`、`finished_at` |
| `conversation_messages` | composite PK `(owner_id,id)`；FK turn/attempt；`role`、`status`、`kind`、`content`、`related_recommendations_json`、`feedback` |
| `conversation_summaries` | `(owner_id,session_id,through_turn_id)` unique；只存长度受限、已脱敏 summary |
| `app_favorites` | PK `(owner_id,professor_id)`；`snapshot_json`、`favorited_at` |
| `app_history` | PK `(owner_id,session_id,type)`；最小 response snapshot、version pins、`created_at` |
| `idempotency_records` | unique `(owner_id,scope,key)`；`request_hash`、`state`、`resource_id`、`response_json`、`expires_at` |

### 6.2 conversation 约束

- root session：`root_session_id=id` 且 source pair 都为空；fork：source pair 同时非空，
  `root_session_id` 指向根 session；不允许 fork-of-fork 递归复制上下文。
- `(owner_id,session_id,ordinal)` unique，ordinal 单调递增。
- session `revision` 每次新增 turn/fork 或删除时加一；request `expected_revision` 不等则 409，
  不自动覆盖。
- attempt 状态机：`queued -> classifying/connecting -> streaming/recommending -> committing -> completed`；
  任一活动态可到 `failed` 或 `interrupted`，终态不可回退。
- 同一 turn 最多一个 active attempt；retry 新建 attempt，不覆盖历史 attempt/message。
- `ConversationStorePort.save_turn` 与 message/attempt 最终提交使用同一 transaction，避免 core context
  已推进但 API projection 未推进。
- internal `TurnSnapshot` 只保存 build/profile/version/result IDs/intent/sanitized summary；不得塞入完整
  evidence、contact、embedding、prompt 或 raw LLM output。

### 6.3 profile/favorite/history

- profile PUT 是整对象替换，server 维护 revision；DELETE 幂等。
- favorite PUT 以 path `professor_id` 为权威，必须等于 body；upsert 幂等。
- favorite/history 是当时的 UI snapshot，不作为事实权威；读取教授事实仍走 ACTIVE build。
- history POST 相同 `(owner_id,session_id,type)` upsert；不得保存 internal diagnostics、contacts 或证据全文。

### 6.4 远端资料清理

`DELETE /account/remote-data` 在一个 transaction 内：

1. `SELECT ... FOR UPDATE` identity；
2. 统计各 application bucket；
3. 删除 profile/session/turn/attempt/message/summary/favorite/history/idempotency 行；
4. 若为 anonymous identity，设置 `revoked_at` 并使当前 token 立即失效；
5. 返回每 bucket 删除数。

不得触碰 catalog SQLite、Neo4j、Qdrant 或 competition knowledge base。transaction 失败则全部回滚；
重复调用使用已撤销 token 应返回 401，不伪造第二个成功响应。

## 7. 幂等、并发和 transaction 边界

### 7.1 turn/attempt 幂等

`POST .../turns` 与 `POST .../attempts` 同时要求 header `Idempotency-Key` 和 body `request_id`：

- key scope 分别为 `turn:{session_id}` / `attempt:{turn_id}`；
- request hash 覆盖 canonical body、owner、target resource，不含 token/headers；
- 同 key + 同 hash + completed：重放保存的最终 event sequence/response，不再次调用 core/LLM；
- 同 key + 同 hash + running：409 `request_in_progress`；
- 同 key + 不同 hash：409 `idempotency_conflict`；
- expired record 可后台清理，但关联 application row 未删时仍不能复用 request_id。

### 7.2 transaction 分段

禁止在数据库 transaction 内等待 embedding/Qdrant/Neo4j/LLM：

1. admission transaction：owner scope、revision、idempotency、创建无敏感原文的 attempt metadata；
2. transaction 外执行 shared content admission 与 core/dispatcher；
3. completion transaction：CAS attempt state，写允许持久化的 message/TurnSnapshot，推进 revision；
4. policy refusal：只写结构化 refusal code/状态或删除 provisional metadata，绝不写被拒原文。

若 completion CAS 发现 cancel 已提交，则丢弃生成结果，不把 attempt 改回 completed。

### 7.3 application task registry

进程内 registry key 为 `(owner_id, attempt_id)`，value 为 task + cancel event。注册/注销必须在
`try/finally`；同 attempt 不可重复注册。shutdown 和 disconnect 都走同一 cancellation 函数。

## 8. Endpoint 行为矩阵

### 8.1 推荐与教授

| Path | Application 调用 | 关键行为 |
|---|---|---|
| `POST /recommendations/mentors` | `runtime.core.recommend` | `prompt -> query_text`；profile 显式映射到 bucketed `StudentContext`；不隐式写 session/history |
| `GET /professors/{id}` | snapshot + `facts_port.get_detail` | public、`include_contacts=False`、review/excluded 表现为 404 |
| `POST /professors/compare` | `auxiliary_generation.compare_professors` | 2–3 个 distinct ID；默认 strict evidence policy |
| `POST /professors/{id}/match-analysis` | `auxiliary_generation.analyze_match` | profile -> `StudentContext`；不返回 internal claims |
| `POST /professors/{id}/outreach-email` | `auxiliary_generation.draft_outreach_email` | fixed professional tone；按请求 locale 选语言；不返回 contacts |

`session_id` 在 mentor recommendation request 中只用于 owner-scoped context lookup/response correlation；
该 endpoint 不创建 turn。需要持久化对话时必须使用 session/turn API。

### 8.2 identity 与 application state

| Path | 行为 |
|---|---|
| `POST /identity/anonymous` | 创建 token/cookies，返回 owner；不接受客户端 owner |
| `GET /account/remote-data` | owner-scoped bucket counts + 明确 excluded resources |
| `DELETE /account/remote-data` | §6.4 原子清理与匿名 token 撤销 |
| `GET/PUT/DELETE /profile` | owner-scoped whole-object read/replace/clear |
| `GET /favorites` | 按 `favorited_at DESC, professor_id` 稳定排序 |
| `PUT/DELETE /favorites/{id}` | path/body ID 一致；幂等 upsert/delete |
| `GET/POST/DELETE /history` | 稳定排序、最小 snapshot upsert、全量清理 |
| `DELETE /history/{session_id}` | owner-scoped 单 session history 删除 |

### 8.3 durable conversation API

| Path | 行为 |
|---|---|
| `POST/GET /chat/sessions` | create root session / list non-deleted root sessions |
| `GET/DELETE /chat/sessions/{id}` | aggregate projection / soft-delete owned session tree |
| `GET /chat/sessions/{id}/turns` | 仅该 session 自有 turns/messages；fork 不泄漏 source 内容 |
| `POST /chat/sessions/{id}/turns` | revision + idempotency；SSE 执行 `ConversationDispatcher.dispatch` |
| `GET/POST /chat/sessions/{id}/forks` | list/create-or-reuse direct fork；source turn 必须属于 source session |
| `POST /chat/turns/{id}/attempts` | 对 failed/interrupted turn 新建 attempt；completed 默认拒绝 |
| `POST /chat/attempts/{id}/cancel` | owner scope + CAS + task cancel；重复 cancel 幂等返回当前终态 |
| `PATCH /chat/messages/{id}/feedback` | 只允许 assistant done message；feedback enum 严格 |

### 8.4 legacy compatibility API

- `POST /chat/messages`：调用与 durable API 相同的 application service，等待完成后返回 JSON；
  contract 没有幂等 key，因此不承诺网络重试去重。不得另写一套 dispatcher。
- `GET /chat/stream`：兼容只读 SSE；校验 owned session、加载 context，但 GET 不创建/修改
  session/turn/message。断连仍取消 generation task。
- `/chat/route`、`/chat/quick-actions`：本阶段 deferred，直到独立 core service 落地。

## 9. Internal model 映射

### 9.1 UserProfile -> StudentContext

允许映射：

```text
degree_stage -> education_stage
school -> school
major -> major
score.gpa_bucket -> gpa_bucket
score.rank_bucket -> rank_bucket
research_interests -> research_interests
research + highlights -> bounded achievements_summary
competitions -> bounded competition_experience_summary
```

`name`、`gender`、raw GPA/rank 不进入 `StudentContext`。summary 使用确定性模板、长度上限和稳定顺序，
不调用 LLM。完整 profile 只存 application DB，不进入事实层。

### 9.2 推荐响应

- internal entity ID -> `professor_id`；display fields 显式映射；
- `match_score` clamp 到 `[0,1]`；`match_level` 使用 contract enum 的确定性阈值，阈值放在
  versioned adapter config，不在 handler；
- explanation 的 supported reason/limitations 映射公开字段；无证据内容不得补写营销文案；
- `session_id` 使用请求 correlation/session；无 session 时生成 response correlation UUID，
  但不据此创建数据库 session；
- internal warnings 中 severity=error 走 §10；warning 不泄漏 operator_action 或 dependency exception。

### 9.3 professor/auxiliary

Professor、comparison、match、email adapter 只输出 OpenAPI 字段；contacts、risk flags、quality findings、
claims、source refs 均不因“内部已有”而自动公开。

## 10. HTTP/error mapping

| 类别 | HTTP | `error_code` |
|---|---:|---|
| malformed JSON/content-type/body too large | 400/413/415 | `invalid_http_request` |
| Pydantic/domain input invalid | 422 | `invalid_request` |
| token 缺失/无效/撤销 | 401 | `unauthenticated` |
| CSRF/权限不足 | 403 | `forbidden` / domain unauthorized code |
| owner-scoped resource 或 ACTIVE professor 不存在 | 404 | `not_found` |
| revision/idempotency/active-attempt 冲突 | 409 | stable application code |
| content policy refusal | 422 | `content_policy_refusal` |
| rate limit | 429 | `rate_limited` |
| request/core timeout | 504 | `request_timeout` |
| catalog/Qdrant/Neo4j/LLM/PostgreSQL 暂不可用 | 503 | 原 domain dependency code |
| 未分类异常 | 500 | `internal_error` |

middleware 只记录安全 code 与 exception type；500 response 不含 traceback、SQL、DSN、文件路径或 raw input。

## 11. SSE 协议与取消

durable turn/attempt stream event 顺序：

```text
ack -> route? -> delta* -> completed
                      \-> error
任一活动态 -----------> interrupted
```

每个 event 的 JSON data 必含 `session_id`、`turn_id`、`attempt_id`、`revision`、`seq`；
`seq` 从 0 单调递增。`ack` 在 admission commit 后发送；`completed` 只能在 completion transaction
提交后发送。delta 可以是单个完整 chunk；R7b 不伪装 token-level streaming。

响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache, no-transform`、
`X-Accel-Buffering: no`。每隔配置秒发送 comment heartbeat，不含用户数据。

`ConnectionResetError`、`CancelledError`、write timeout 或显式 cancel 必须：

1. set cancel event；
2. cancel 并 await generation task；
3. CAS attempt 到 interrupted；
4. 不再写 delta/completed，不后台继续计费；
5. `finally` 从 registry 移除 task。

content-policy refusal 只发结构化 `error`，event/DB/log 均不包含被拒原文。

## 12. 隐私、日志与持久化

允许日志：request/owner 的不可逆短 hash、build/profile/fingerprint、operation、HTTP status、长度/语言/
过滤摘要、计数、phase latency、error code、pool saturation、attempt state。

禁止日志：token/cookie/CSRF、DSN/password、query/message/profile 原文、raw GPA/rank、embedding、prompt、
raw LLM output、完整 evidence/source document、联系人、被拒绝原文。

持久化规则：

- 正常 conversation 为产品功能可保存 message，但必须 owner-scoped、可删除、不得复制到日志；
- policy-refused 原文不保存；
- history/favorite 只存 OpenAPI 所需最小 snapshot；
- diagnostics/evidence/contact 不进入 profile/history/idempotency response cache；
- idempotency response cache 与业务数据执行相同 owner deletion 和 TTL 清理。

## 13. 测试策略

### 13.1 单元/契约

- OpenAPI owned/deferred/external manifest、operationId、error response、unknown-field tests；
- DTO round-trip 和逐字段 mapping golden tests；
- profile -> bucketed StudentContext，raw score 拒绝/不落盘；
- error-code/status mapping exhaustive parameterized tests；
- handler thin-boundary：用 fake application service，断言无排序/过滤/prompt/SQL。

### 13.2 authentication/security

- bearer、cookie、双 credential 一致/冲突、过期/撤销/无 provider；
- CSRF token + Origin、Secure/SameSite/HttpOnly cookie 属性；
- contacts/review/diagnostics 权限来自 principal，request 自授予无效；
- 每个 repository 的 same-owner success 与 cross-owner 404；
- token、DSN、query、profile、refusal 原文不出现在 caplog/response。

### 13.3 PostgreSQL integration

使用真实 PostgreSQL 16（`docker/compose.yaml`），不以 SQLite 替代 JSONB、row lock、unique/CAS 测试：

- migration empty -> head、重复 upgrade、head mismatch fail-fast；
- profile/favorite/history transaction 与幂等；
- 两个并发 expected_revision 只有一个成功；
- idempotency replay/running/conflicting hash；
- fork owner/source invariants；
- completion-vs-cancel race 不产生双终态；
- remote delete 全回滚、bucket count、token revoke、excluded stores untouched。

### 13.4 HTTP/SSE

- `aiohttp.test_utils` 覆盖所有 implemented path 的 schema/status/content-type；
- SSE event 顺序、必填 IDs/revision/seq、heartbeat、单 delta 合法；
- client disconnect、explicit cancel、shutdown 均取消 task 且无 pending task；
- completed replay 不再次调用 fake core/LLM；
- policy refusal 覆盖 recommendations、durable/legacy chat、match/email/compare：稳定 code、无生成后续、
  无敏感日志、PostgreSQL 无被拒文本。

### 13.5 回归门禁

```text
uv run pytest tests/dext_recommend -q --tb=short
uv run pytest -q --tb=short
PostgreSQL integration suite
OpenAPI owned-path contract suite
```

真实 embedding/Qdrant/Neo4j/LLM 仍属于 R7c，不把外部网络测试设为 R7b 日常 gate。

## 14. TDD 实现顺序

1. OpenAPI 缺口修正 + owned-path manifest + contract loader；
2. `AppSettings`、migration、async engine lifecycle；
3. Principal/anonymous token/cookie/CSRF + auth middleware；
4. owner-scoped models/repositories + remote delete；
5. profile/favorite/history routes；
6. runtime `conversation_store` injection seam + PostgreSQL ConversationStorePort；
7. recommendation/professor/auxiliary DTO adapters 和 routes；
8. session/fork/turn/attempt transaction 与 idempotency；
9. SSE/task registry/cancel/shutdown；
10. legacy chat compatibility；
11. 全量 security/privacy/concurrency/contract 回归；
12. 更新 `.env.example`、migration/runbook 与 R7c handoff。

每步先写失败测试，再写最小实现；repository 与 route 不在同一个大提交中落地。

## 15. 验收清单

- [ ] implemented/deferred/external path 集合完整、互斥，并与 OpenAPI 一致；
- [ ] 所有 implemented path 有 contract test、稳定 operationId 与错误响应；
- [ ] app startup 对 DB migration/R7a readiness fail-fast，shutdown 无 task/client/engine 泄漏；
- [ ] 无 credential/错误 credential/跨 owner/CSRF 全 fail-closed；
- [ ] ViewerPermissions 只从 Principal 派生；
- [ ] PostgreSQL schema、owner composite constraints、revision、idempotency 和状态机测试通过；
- [ ] transaction 内不等待外部 AI/vector/graph 调用；
- [ ] SSE disconnect/cancel/shutdown 均停止生成与计费；
- [ ] policy-refused 原文不进日志、DB、event 或 response；
- [ ] remote delete 原子清理 application state 且不触碰事实存储；
- [ ] handler 保持薄边界，core 可绕过 HTTP 独立测试；
- [ ] `tests/dext_recommend`、全仓测试、PostgreSQL integration、OpenAPI contract 全绿；
- [ ] deferred 三端点没有占位实现，后续 core prerequisite 明确；
- [ ] R7c handoff 记录真实依赖、负载、故障演练与残余风险。

## 16. R7a handoff 风险（不阻塞 R7b 编码，必须在 R7c 前关闭）

1. readiness 目前读取 dense dimension，但未显式验证 named sparse vector config；R7c 前应让
   dense-only ACTIVE collection 在 startup fail-fast。
2. background refresh 接受新 snapshot 时未再次校验 runtime embedding provider/model；不兼容新 build
   可能到首次请求才暴露。应在 provider promote 前校验或要求 runtime restart。
3. `coverage_flags_by_build_id` 只冻结 startup build；refresh 到新 build 后会安全降级 org-unit filter，
   但会损失质量。应与 snapshot/report 原子更新或提供按 build 的动态只读 accessor。
4. live runtime integration test 仍是 opt-in；它不是 R7b fake-client gate，但必须进入 R7c 真实依赖验收。
