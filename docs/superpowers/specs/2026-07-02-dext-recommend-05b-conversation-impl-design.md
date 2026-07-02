# 阶段 5：dext_recommend conversation adapter 实现设计

> 状态：设计稿；待 TDD 落地
>
> 高层目标：[阶段 5 conversation adapter](2026-06-30-dext-recommendation-05-conversation-design.md)
>
> 前置依赖：[R4b professor facts](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md) 事实包稳定
>
> 后续阶段：[R6 auxiliary generation](2026-06-30-dext-recommendation-06-auxiliary-generation-design.md) 可并行；共同进入 [R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md)
>
> 日期：2026-07-02

## 1. 目标与范围

实现 session/turn/fork 上下文校验、implicit intent 分类、`ConversationContext` 组装与 `detail_followup` 受约束生成回答。
推荐核心保持无状态、可独立调用；R3 继续拥有五种 intent 的推荐执行语义，R5 不复制排序、过滤、same-field 或 oversample 逻辑。
本阶段不实现 PostgreSQL 持久化，也不实现匹配/套磁/对比生成（归 R6）。

R5 的核心定位是 **fact-based 受约束生成的第一个消费者**：`detail_followup` 走 `dext_grounded` 的
`LLMGenerationPort` + `CitationValidator` + `SafetyGuard` 管线，R6 的 match/email/compare 复用同一管线，只换 prompt 与 user_inputs。

### 1.1 决策摘要

- 模块组织选方案 A：新增 `core/conversation.py` 单一编排文件，内部用纯函数拆分职责（`_validate_context`、`_classify_implicit`、`_assemble_context`、`resolve_detail_followup`），与 R4b reader/adapter 两层分层一致。
- `detail_followup` 在 R5 完整落地：锚定验证 + `ProfessorDetail.fact_bundle` 输入 + 受约束生成回答。
- implicit intent 走 `dext_grounded.LLMGenerationPort` 的 JSON 受约束分类器，不新建第二条 LLM 管线。
- explicit intent 状态转移失败由 R3 的 fallback+warning 收紧为**结构化错误**（按 R5 §4 "返回结构化错误而非猜测 intent"）。
- `ConversationStorePort` 为 store-neutral port + fake；真实 PostgreSQL 归 R7b。

## 2. ConversationContext 校验与组装

`ConversationContext` dataclass（`models.py`）字段已与 overview §8 完全对齐，**不改字段**。R5 新增校验规则与组装职责，落在 `core/conversation.py` 的纯函数 `_validate_context` / `_assemble_context`。

### 2.1 校验规则（`_validate_context`，纯同步）

| 检查 | 失败行为 |
|---|---|
| `intent_source` 非 `explicit|implicit|null` | 结构化错误 `invalid_intent` |
| `intent` 非白名单五枚举之一（含 `new_search|more_mentors|same_field|refine_direction|detail_followup`） | 结构化错误 `invalid_intent` |
| `intent_source=explicit` 但 `intent=None` | 结构化错误 `invalid_intent`（explicit 必须带枚举） |
| `intent_source=implicit` 但 `intent_confidence` 不在 `[0,1]` | 结构化错误 `invalid_intent` |
| `session_id`、`turn_id` 二者要么都有要么都无 | 结构化错误 `invalid_conversation_state` |
| `main_session_id`/`source_turn_id` 表达 fork：二者必须同时出现或同时缺席 | 结构化错误 `invalid_conversation_state` |
| `prior_result_entity_ids` 重复 ID 去重（保留首次顺序） | 静默规范化，不报错 |

### 2.2 组装职责（`_assemble_context`，纯同步）

接收上游（HTTP adapter / 应用层）传入的脱敏上下文片段，组装成不可变 `ConversationContext`。组装只做字段拼装与去重，**不补造** `anchor_entity_id`、不推断 `intent`——意图来源（explicit/implicit）由调用方显式声明；未声明者（`intent_source=None` 且 `intent=None`）走 §3 implicit 分类。

### 2.3 fork 关系边界

`main_session_id`、`source_turn_id` 由应用层持久化表达 fork；推荐核心只**消费** `ConversationContext`，不持久化、不写 fork 树。`ConversationStorePort`（§5）仅保存"回到本会话所需的最小快照字段"，不存完整证据包。

### 2.4 不变量

`ConversationContext` 已是 `frozen=True, slots=True`，`prior_result_entity_ids` 经 `__post_init__` 冻结为 tuple。R5 不新增字段，仅新增校验函数。

## 3. explicit intent 路由与严格状态转移

收紧 `core/intent.py`，从"fallback new_search+warning"改为"结构化错误"。**R3 排序/过滤/same-field/oversample 语义不动**——只改转移失败的终止行为。

### 3.1 `RecommendRoute` 调整

```text
RecommendRoute
  intent: str                                # 五枚举之一（resolve 后）
  exclude_entity_ids: tuple[str, ...]
  anchor_entity_id: str | None
  refine_merge: bool
  detail_followup: bool                      # 新增：R5 负责 detail_followup 执行
  errors: tuple[RecommendationError, ...]    # 新增：结构化错误（替代转移失败 warning）
  warnings: tuple[RecommendationWarning, ...]    # 保留：非终止性提示（如 review 降权）
```

`unsupported` 字段删除——`detail_followup` 不再短路为 unsupported，改为 `detail_followup=True` 标记后由 `core/conversation.py` 接管执行。

### 3.2 explicit 路由规则（`resolve_recommend_route`，纯同步）

| intent | 必需上下文 | 缺失/失败行为 |
|---|---|---|
| `new_search` | 无 | 通过 |
| `more_mentors` | `prior_result_entity_ids` 非空 | 结构化错误 `more_mentors_requires_prior` (error) |
| `same_field` | `anchor_entity_id` 非空 | 结构化错误 `same_field_requires_anchor` (error) |
| `refine_direction` | 无（合并 QU preferred_*） | 通过 |
| `detail_followup` | `anchor_entity_id` 非空 | 结构化错误 `detail_followup_requires_anchor` (error) |

### 3.3 anchor ACTIVE-build 校验位置

- `same_field`：保留 `hydrate` 校验（R3 已实现于 `service.py`，轻量）；anchor 缺失 / inactive / excluded / 无 approved topics → 结构化错误 `anchor_not_in_active_build`（替代当前 fallback+warning）。
- `detail_followup`：用 `get_detail` 校验（R5 新增，需完整事实包）；缺失/inactive/excluded 抛 `ProfessorFactNotFound` → 结构化错误 `anchor_not_in_active_build`。

### 3.4 新增错误码（`errors.py`）

- `INVALID_CONVERSATION_STATE = "invalid_conversation_state"`
- `MORE_MENTORS_REQUIRES_PRIOR = "more_mentors_requires_prior"`
- `SAME_FIELD_REQUIRES_ANCHOR = "same_field_requires_anchor"`
- `DETAIL_FOLLOWUP_REQUIRES_ANCHOR = "detail_followup_requires_anchor"`
- `ANCHOR_NOT_IN_ACTIVE_BUILD = "anchor_not_in_active_build"`
- `INTENT_CLASSIFICATION_UNAVAILABLE = "intent_classification_unavailable"`（§4 用）
- `FOLLOWUP_GENERATION_UNAVAILABLE = "followup_generation_unavailable"`（§5 用）

### 3.5 `RecommendationCore.recommend` 编排调整

- `resolve_recommend_route` 返回 `errors` 非空 → 立即返回 error response（不走 recall），保留此前 warning。
- `route.detail_followup=True` → `recommend` 返回结构化 error `unsupported_for_recommend_core`（detail_followup 由 §5.6 的 `answer_followup` 独立入口处理，不经 `recommend`）。
- 现有 `intent="bogus"` fallback 行为改为 `INVALID_INTENT` error（不再静默 fallback new_search）。

### 3.6 对 R3 现有测试的影响

`test_recommend_intent.py` 中四个测试断言会变，改为断言 error response：
- `test_more_mentors_without_prior_falls_back_with_warning`
- `test_same_field_without_anchor_falls_back_with_warning`
- `test_invalid_intent_falls_back_to_new_search_with_warning`
- `test_detail_followup_is_unsupported`

这是有意的语义收紧，按 R5 §4 "返回结构化错误而非猜测 intent"。

## 4. implicit intent LLM 分类器

自由文本追问 → `{intent, confidence, rationale}`，走 `dext_grounded.LLMGenerationPort`。

### 4.1 入口判定

`ConversationContext.intent_source="implicit"`，或 `intent_source=None` 且 `intent=None`（应用层未声明意图的自由文本追问）由 `core/conversation.py:_classify_implicit` 接管。`intent_source="explicit"` 不进分类器（§3 直接路由）。

### 4.2 LLM 调用契约

```text
system_prompt_id = "dext_recommend.implicit_intent.v1"
user_inputs = {
    "query_text": <脱敏后的追问文本, 长度上限按 query_max_chars>,
    "conversation_summary": <脱敏后的会话摘要, 不含完整证据包/用户档案原值>,
    "has_anchor": <bool, anchor_entity_id 是否存在>,
    "has_prior_results": <bool, prior_result_entity_ids 是否非空>,
}
fact_bundle = FactBundle.empty()              # grounded §4: 可为空（纯分类场景）
student_context = None                       # implicit 分类不消费用户档案
json_schema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "confidence", "rationale"],
    "properties": {
        "intent": {"enum": ["new_search","more_mentors","same_field","refine_direction","detail_followup"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string", "maxLength": 200}
    }
}
generation_profile_version = <版本化 profile version>
```

`FactBundle` 构造器字段无默认值但接受空集合（`tuple(x) if x is not None else ()`），R5 在 `core/conversation.py` 直接构造空 bundle：`FactBundle(build_id=snapshot.build_id, subject_id="implicit-intent", facts=(), source_refs=())`。grounded 契约已允许空 bundle（§4 "可为空"）。

### 4.3 分类结果处理（`_classify_implicit`，async）

| 情况 | 行为 |
|---|---|
| LLM 返回合法 JSON，`intent` 在白名单，`confidence >= threshold` | 写回 `ConversationContext.intent/confidence`，转 §3 explicit-equivalent 路由（再做状态转移校验） |
| `confidence < threshold`（版本化阈值，默认 `0.6`） | 结构化错误 `needs_clarification` (warning)，不触发宽召回 |
| `intent` 枚举非法 / JSON 无法解析 | 结构化错误 `needs_clarification` (warning) |
| 状态转移校验失败（如分类出 `more_mentors` 但缺 prior） | 结构化错误（按 §3.2 规则，`more_mentors_requires_prior` 等） |
| LLM provider 不可用 / 超时 | 结构化错误 `intent_classification_unavailable` (error) |

### 4.4 不变量

- implicit 分类的 `rationale` **不进入** `RecommendResponse`（避免泄露 LLM 内部推理给 App 侧），只用于评测日志（按 overview §18 脱敏）。
- `confidence` 写入 `ConversationContext.intent_confidence`，随上下文消费，不出现在推荐卡片字段。
- 阈值 `implicit_intent_confidence_threshold`（默认 0.6）与 prompt_id 进 `generation_profile_version`，不在 handler 改常数。

### 4.5 与 §3 的衔接

implicit 分类成功后，得到的 `intent` 走与 explicit 相同的状态转移校验（§3.2 表格），统一从 `resolve_recommend_route` 出口走。区别仅在 `intent_source` 标记（`implicit`）和 `intent_confidence` 是否填充。

## 5. detail_followup 受约束生成

R5 第一个 fact-based 受约束生成消费者。`detail_followup` 路由由 §3 校验通过后（`anchor_entity_id` 非空），由 `core/conversation.py:resolve_detail_followup` 接管，**不进入** recall/rerank pipeline。

### 5.1 执行流程（`resolve_detail_followup`，async）

```text
1. snapshot = snapshot_port.get_snapshot()        # 同 §3 路由前置
2. anchor detail = await facts_port.get_detail(
       snapshot, ctx.anchor_entity_id,
       include_contacts=request.include_contacts and vp.include_contacts,
       viewer_permissions=vp,
   )                                              # 复用 R4b adapter
   - ProfessorFactNotFound → 结构化错误 anchor_not_in_active_build (error)
   - review entity 且 vp.can_view_review=False → unauthorized_review (error)
3. fact_bundle = anchor_detail.fact_bundle         # R4b 已组装, contacts 已剥离
4. result = await llm_port.generate(
       system_prompt_id="dext_recommend.detail_followup.v1",
       user_inputs={"question": <脱敏追问>, "display_name": anchor_detail.display_name},
       fact_bundle=fact_bundle,
       student_context=request.student_context,    # 可为空
       json_schema=<受约束回答 schema 或 None 走 markdown>,
       generation_profile_version=<版本化>,
   )                                              # 走 dext_grounded 管线
5. cited_result = CitationValidator(rules).validate(
       result, fact_bundle, request.student_context,
   )                                              # grounded §5.2; 返回新 GenerationResult
6. safe_result = SafetyGuard(rules).inspect(
       cited_result, domain="recommend",
       include_contacts=request.include_contacts and vp.include_contacts,
   )                                              # 概率承诺/伪造引用/越权联系方式拦截
7. 组装 DetailFollowupResponse
```

`CitationValidator.validate(result, fact_bundle, student_context) -> GenerationResult` 与
`SafetyGuard.inspect(result, *, domain, include_contacts) -> GenerationResult` 均为纯同步，返回新 `GenerationResult`
（`dataclasses.replace`）。`domain="recommend"` 触发推荐侧额外规则（无录取概率、不伪造经历）。

### 5.2 `DetailFollowupResponse`（新增 model，不复用 `RecommendResponse`）

```text
DetailFollowupResponse
  build_id: str
  ranking_profile_version: str
  embedding_fingerprint: str          # snapshot 的, 用于版本对账
  taxonomy_version: str | None
  anchor_entity_id: str
  anchor_display_name: str
  answer: str                        # SafetyGuard 清洗后的最终文本
  claims: tuple[Claim, ...]          # 逐条分类, 供 App 侧渲染 fact/advice/uncertain
  cited_refs: tuple[SourceRef, ...]  # 来自 fact_bundle 的规范引用
  warnings: tuple[RecommendationWarning, ...]
  phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()
```

### 5.3 不变量

- `answer` 中每条 `fact` 类 claim 的 `fact_refs` 必在 `fact_bundle.source_refs` 内（grounded §5.2 CitationValidator 强制）。
- `answer` 不得含 anchor_detail.contacts 字段（SafetyGuard 越权联系方式拦截 + R4b 已保证 fact_bundle 不含 contacts，双重防护）。
- LLM 不得编造导师事实：`FactItem` 缺失时必须 `uncertain`，不补造（grounded §3/§5.2）。
- snapshot pinned 一次，`get_detail` 与 `generate` 共用同一 snapshot（overview §9 一致性）。

### 5.4 失败模式（grounded §8 + R5 §7）

- LLM provider 不可用 → `followup_generation_unavailable` (error)
- JSON 无法解析 → `generation_parse_error` (error)
- 引用校验全部失败 → `no_grounded_output` (error)，不返回无引用纯 LLM 文本
- 事实包为空却要求 fact 输出 → `insufficient_facts` (warning/error，按 grounded §8）

### 5.5 与 R6 的复用

`resolve_detail_followup` 走的 `get_detail → generate → CitationValidator → SafetyGuard` 序列正是 R6 match/email/compare 将复用的同一管线。R5 落地后 R6 只需新增各自的 `system_prompt_id` + `user_inputs` 组装，不改管线。这是 §1 选方案 A 的核心理由。

### 5.6 `RecommendationCore` 接口

新增 `async answer_followup(request, *, viewer_permissions) -> DetailFollowupResponse` 方法，与 `recommend` 并列。
`recommend` 仍只处理四种 recommend-path intent；`detail_followup=True` 的请求由调用方（HTTP 层）按 intent 分派直接调 `answer_followup`，
**不**经 `recommend`。这样 `recommend` 的 `RecommendResponse` 返回类型保持单一，不与 `DetailFollowupResponse` 混在同一入口。
`resolve_recommend_route` 仍由两条路径共用：`answer_followup` 内部也先调它做 explicit 状态转移校验（§3.2），
`detail_followup` 分支返回 `route.detail_followup=True` 后由 `resolve_detail_followup` 接管。

## 6. ConversationStorePort + fake

§6 要求 R5 定义 store-neutral repository port 与 fake；真实 PostgreSQL schema/repository 归 R7b。R5 只固化会话/fork 关系的最小字段，不存完整证据包与用户档案（§6 "写历史时只保存 response snapshot 所需的最小字段"）。

### 6.1 端口定义（`ports/conversation_store.py`，新增）

```text
@runtime_checkable
class ConversationStorePort(Protocol):
    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None
    async def save_turn(self, session_id: str, turn_id: str,
                        context: ConversationContext,
                        snapshot: TurnSnapshot) -> None
    async def list_prior_entity_ids(self, session_id: str, limit: int = 50) -> tuple[str, ...]
    async def resolve_fork(self, main_session_id: str, source_turn_id: str) -> ConversationContext | None
```

### 6.2 `TurnSnapshot`（新增 model，最小字段）

```text
TurnSnapshot
  build_id: str
  ranking_profile_version: str
  result_entity_ids: tuple[str, ...]      # 仅 entity ID, 不含完整卡片/证据
  intent: str | None
  intent_source: str | None
  created_at: str                          # ISO-8601
```

### 6.3 边界约束

- `TurnSnapshot` **不含** `FactBundle`、`SourceRef`、`StudentContext`、完整 query_text、联系方式（overview §6/§18 隐私）。证据需重读时从 ACTIVE build 重新组装，不历史落库。
- `ConversationContext` 经 store 往返必须保持字段一致（`main_session_id`/`source_turn_id` 表达 fork）。
- store **不持有** fork 树结构——`resolve_fork` 仅按 `(main_session_id, source_turn_id)` 取回源 turn 上下文，不递归遍历。
- 推荐核心通过 store **只读消费**上下文；`save_turn` 由应用层（HTTP adapter）在响应后调用，core 自身不写 store（保 §6 "推荐核心不保存 session、不写历史"）。

### 6.4 `FakeConversationStorePort`（`ports/_fakes.py`，新增）

```text
FakeConversationStorePort
  __init__(initial: dict[tuple[str, str|None], ConversationContext] | None = None)
  load_context(session_id, turn_id) -> ConversationContext | None
  save_turn(session_id, turn_id, context, snapshot) -> None  # 写入内存 dict
  list_prior_entity_ids(session_id, limit) -> tuple[str, ...]
  resolve_fork(main_session_id, source_turn_id) -> ConversationContext | None
  saved_turns: list[dict]   # 测试断言用
```

### 6.5 R5 范围内 store 的使用

- `ConversationRouter` 在 implicit 分类前可 `load_context` 取回前序会话摘要（喂给 §4.2 的 `conversation_summary`），但 **R5 默认不强制**——应用层传完整 `ConversationContext` 时 store 可不参与。store 在 R5 主要用于 fake-driven 单测覆盖"上下文往返一致性"。
- R7b 才接 PostgreSQL；R5 的 `ConversationStorePort` 是契约占位 + fake，保证 R7b 只填实现不改接口。

### 6.6 Re-export

`ConversationStorePort`、`FakeConversationStorePort`、`TurnSnapshot` 加入 `dext_recommend/__init__.py` 的 `__all__`。

### 6.7 `RecommendDeps` 调整

`conversation_store` 字段保持可选——`RecommendDeps.conversation_store: ConversationStorePort | None = None`，core 内部判空跳过 `load_context`。避免强依赖 store 才能 recommend。

## 7. 验收与测试边界

按 R5 §7 验收标准 + R0/R1 接受门槛（单模块绿、不要求全项目；LLM 测试用真实 key）。

### 7.1 测试文件划分

| 文件 | 覆盖 |
|---|---|
| `test_recommend_conversation_context.py` | §2 `_validate_context`/`_assemble_context`：字段校验、session/turn 同进同出、fork 同进同出、prior 去重、不可变 |
| `test_recommend_intent.py`（改） | §3 严格状态转移：more_mentors 缺 prior→error、same_field/detail_followup 缺 anchor→error、invalid intent→error（替代旧 fallback 断言）；R3 排序语义不动 |
| `test_recommend_conversation_implicit.py` | §4 LLM 分类：合法 JSON+高置信→路由、低置信→needs_clarification、枚举非法→needs_clarification、状态转移失败→error、LLM 不可用→intent_classification_unavailable；走 `FakeLLMGenerationPort` 注入 `GenerationResult(output={...})` |
| `test_recommend_detail_followup.py` | §5 fact-based 生成：anchor 校验、fact_bundle 注入、CitationValidator 剔除伪造引用、SafetyGuard 拦截概率承诺/越权联系方式、`DetailFollowupResponse` 不变量、ProfessorFactNotFound→error |
| `test_recommend_conversation_store.py` | §6 store 往返：load/save 一致性、list_prior、resolve_fork、TurnSnapshot 不含敏感字段 |
| `test_recommend_immutability.py`（改） | 新增 `DetailFollowupResponse`、`TurnSnapshot`、`ConversationRouter` 深度不可变断言 |
| `test_recommend_import_boundary.py`（改） | `dext_recommend` 不 import `dext`/`dext_graph`/`dext_monitor`/`dext_competition`；`dext_grounded` 允许 |

### 7.2 LLM 测试边界（CLAUDE.md 测试策略 + R0/R1 门槛）

- implicit 分类器与 detail_followup 生成**都是 LLM-touching**，按 CLAUDE.md 用真实 `DEEPSEEK_API_KEY`，不 MockLLM/FakeLLM 做 record-replay。
- `FakeLLMGenerationPort` 是 `dext_grounded` 提供的**契约测试替身**（注入预设 `GenerationResult` 验证管线编排，非 LLM 行为模拟），用于：状态转移、错误码、CitationValidator/SafetyGuard 编排——这些是纯逻辑，不需要真 LLM。
- 真实 LLM 只用于少量"端到端" smoke：implicit 分类准确率样本 + detail_followup groundedness 样本（overview §16 评测契约口径）。这些样本在缺少 `DEEPSEEK_API_KEY` 时 skip，单模块绿不要求必跑。

### 7.3 async 边界（R5 §7 最后一条）

- `ConversationRouter.classify_implicit`、`resolve_detail_followup`、`ConversationStorePort.*` 为 `async def`。
- `_validate_context`、`_assemble_context`、`resolve_recommend_route`、`CitationValidator.validate`、`SafetyGuard.apply` 保持同步。
- `RecommendationCore.answer_followup` 为 async，与 `recommend` 并列。

### 7.4 import boundary

`core/conversation.py` 可 import `dext_grounded`（CitationValidator/SafetyGuard/LLMGenerationPort）、`core/intent.py`、`models.py`、`ports/`；**不** import `api/`、具体 adapter、`dext_graph`。

### 7.5 评测契约口径（R5 §7 + overview §16）

- `eval/` 落地 `implicit conversation routing accuracy` 与 `explicit route contract pass rate` 的**口径与样本形状**（不要求线上规模基线值，R7c 才跑全量）。
- 样本绑定 `generation_profile_version`、`build_id`、`commit hash`。

### 7.6 非目标（重申）

- PostgreSQL schema/repository（R7b）。
- match/email/compare 生成（R6）。
- 在线 LLM 事实生成（grounded §6 SafetyGuard 已拦）。
- production composition / HTTP OpenAPI 适配（R7a/R7b）。

## 8. 非目标

- R6 match/email/compare 生成（R5 只为 R6 验证管线可复用，不实现这三个能力）。
- PostgreSQL schema/repository（R7b）。
- 在线 LLM 事实生成、production composition、HTTP OpenAPI 适配。
- fork 树遍历、跨会话长期画像、点击反馈学习（overview §19 非目标）。
