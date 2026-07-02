# 阶段 5：dext_recommend conversation adapter 实现设计

> 状态：已实现；2026-07-02 本地验收通过
>
> 高层目标：[阶段 5 conversation adapter](2026-06-30-dext-recommendation-05-conversation-design.md)
>
> 前置依赖：[R4b professor facts](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md) 事实包稳定
>
> 内容安全增量：implicit intent 与 detail follow-up 均必须复用 `SafetyGuard.inspect_input`/`SafetyGuard.inspect`；命中 `content_policy_refusal` 时返回 terminal error，不降级为澄清或半清洗回答。
>
> 后续阶段：先冻结本文定义的共享 generation profile 与 constrained-generation seam；随后 [R6 auxiliary generation](2026-06-30-dext-recommendation-06-auxiliary-generation-design.md) 可并行实现；共同进入 [R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md)
>
> 日期：2026-07-02

> 验收记录：`uv run pytest tests/dext_grounded/ -q --tb=short` → 134 passed；`uv run pytest tests/dext_recommend/ -q --tb=short -rs` → 380 passed, 1 skipped（live LLM 需显式开关）；`DEXT_RECOMMEND_RUN_LIVE_LLM=1 uv run pytest tests/dext_recommend/test_recommend_llm_live.py -q --tb=short -rs` → 1 passed；`.\.venv\Scripts\python.exe -m pytest -q --tb=short` → 980 passed, 19 skipped。

## 1. 目标与范围

实现 session/turn/fork 上下文校验、implicit intent 分类、`ConversationContext` 组装、统一 conversation dispatch 与 `detail_followup` 受约束生成回答。
推荐核心保持无状态、可独立调用；R3 继续拥有五种 intent 的推荐执行语义，R5 不复制排序、过滤、same-field 或 oversample 逻辑。
本阶段不实现 PostgreSQL 持久化，也不实现匹配/套磁/对比生成（归 R6）。

R5 的核心定位是 **fact-based 受约束生成的第一个推荐域消费者**：`detail_followup` 走共享
`ConstrainedGenerationPipeline`（`SafetyGuard.inspect_input` → `LLMGenerationPort` raw generation → `CitationValidator` → `SafetyGuard.inspect`）。
R5 与 R6 都依赖该共享 seam；R5 不拥有一条只能由 R6 事后复制的私有管线。

### 1.1 决策摘要

- 模块组织选方案 A：新增 `core/conversation.py`，定义统一 `ConversationDispatcher`；纯函数负责 `_validate_context`、`_assemble_context` 与 route 校验，async 方法负责 implicit 分类和 pinned-snapshot dispatch。
- `detail_followup` 在 R5 完整落地：锚定验证 + `ProfessorDetail.fact_bundle` 输入 + 受约束生成回答。
- implicit intent 走共享 `ConstrainedGenerationPipeline` 的 JSON 分类模式，不新建第二条 LLM client；空 FactBundle 分类不执行 fact-claim 校验，但仍执行 schema/安全检查。
- 内容政策拒答是 terminal error：输入拒答不调用 LLM，输出拒答清空 claims/cited refs；推荐侧统一映射为 `content_policy_refusal` severity=`error`。
- conversation 对外统一由 `ConversationDispatcher.dispatch(...) -> ConversationDispatchResult` 处理。它先分类、再 route、最后选择 recommend/detail 分支，消除 HTTP 层在 implicit 分类前无法选入口的问题。
- 为兼容现有 `RecommendResponse`，本阶段不新增第二套 error collection：结构化终止问题继续使用 `RecommendationWarning(code=..., severity="error")`。`RecommendationError` 保留给 readiness/内部诊断，不塞入 `RecommendRoute` 后再丢失。
- `ConversationStorePort` 为 store-neutral port + fake；真实 PostgreSQL 归 R7b。
- recommendation generation profile 在 R5 固化 artifact + loader port；prompt、schema、阈值、token budget 与 grounded rules manifest hash 统一进入 `generation_profile_version`，R6 只扩展 operation 配置。

## 2. ConversationContext 校验与组装

`ConversationContext` dataclass（`models.py`）字段已与 overview §8 完全对齐，**不改字段**。R5 新增校验规则与组装职责，落在 `core/conversation.py` 的纯函数 `_validate_context` / `_assemble_context`。

### 2.1 两阶段校验（`_validate_context`，纯同步）

`_validate_context(context, *, phase: Literal["input", "resolved"]) -> ConversationContext` 返回规范化后的新对象；失败抛内部 `ConversationValidationError(code, safe_message)`，由 dispatcher 转为 `RecommendationWarning(severity="error")`。禁止在纯函数内构造 HTTP 响应。

| 检查 | 失败行为 |
|---|---|
| `intent_source` 非 `explicit|implicit|null` | 结构化错误 `invalid_intent` |
| `intent` 非白名单五枚举之一（含 `new_search|more_mentors|same_field|refine_direction|detail_followup`） | 结构化错误 `invalid_intent` |
| `intent_source=explicit` 但 `intent=None` | 结构化错误 `invalid_intent`（explicit 必须带枚举） |
| `intent_source=None` 但 `intent` 或 `intent_confidence` 非空 | 结构化错误 `invalid_intent`（禁止绕过来源边界） |
| input phase：`intent_source=implicit` | 只允许 `intent=None AND intent_confidence=None`，表示待分类；部分填充或调用方预填分类结果均为 `invalid_intent` |
| resolved phase：`intent_source=implicit` | `intent` 必须在白名单且 `intent_confidence` 必须在 `[0,1]` |
| `intent_source=explicit` 且 `intent_confidence` 非空 | 结构化错误 `invalid_intent`（explicit 不伪造模型置信度） |
| `session_id`、`turn_id` 二者要么都有要么都无 | 结构化错误 `invalid_conversation_state` |
| `main_session_id`/`source_turn_id` 表达 fork：二者必须同时出现或同时缺席 | 结构化错误 `invalid_conversation_state` |
| `prior_result_entity_ids` 重复 ID 去重（保留首次顺序） | 静默规范化，不报错 |

`intent_source=None AND intent=None AND intent_confidence=None` 在 input phase 规范化为待分类 implicit 状态；dispatcher 内部用 `dataclasses.replace(..., intent_source="implicit")` 表达，不由 HTTP adapter 猜 intent。

### 2.2 组装职责（`_assemble_context`，纯同步）

接收上游（HTTP adapter / 应用层）传入的脱敏上下文片段，组装成不可变 `ConversationContext`。组装只做字段拼装与去重，**不补造** `anchor_entity_id`、不推断具体 `intent`。explicit 必须由可信枚举动作明确声明；未声明者统一进入 implicit 分类。

函数签名固定为：

```text
_assemble_context(*, session_id, turn_id, main_session_id, source_turn_id,
                  anchor_entity_id, intent, intent_source, intent_confidence,
                  prior_result_entity_ids) -> ConversationContext
```

返回前必须执行 input-phase validation，避免 assembler 与 validator 形成两套规则。

### 2.3 fork 关系边界

`main_session_id`、`source_turn_id` 由应用层持久化表达 fork；推荐核心只**消费** `ConversationContext`，不持久化、不写 fork 树。`ConversationStorePort`（§6）仅保存"回到本会话所需的最小快照字段"与受限脱敏摘要，不存完整证据包。

### 2.4 不变量

`ConversationContext` 已是 `frozen=True, slots=True`，`prior_result_entity_ids` 经 `__post_init__` 冻结为 tuple。R5 不新增字段，仅新增校验函数。

### 2.5 脱敏会话摘要

implicit 分类需要的 `conversation_summary` 不从 `ConversationContext` 猜测，也不重放原始 messages。新增不可变 DTO：

```text
ConversationSummary
  session_id: str
  through_turn_id: str | None
  text: str                    # 最多 generation profile 配置的 summary_max_chars
  created_at: str              # UTC ISO-8601
```

- `ConversationDispatcher.dispatch(..., conversation_summary=None)` 优先消费调用方传入的摘要；未传且 store 可用时调用 `load_summary`。
- 摘要只能包含 intent、选中过的 entity IDs、公开偏好 bucket 与用户明确给出的非敏感约束；不得包含联系人、完整 StudentContext、原始长 query、FactBundle、embedding 或秘密。
- 没有摘要时传空字符串，不得把 `ConversationContext` 序列化后冒充自然语言摘要。

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
  terminal_issues: tuple[RecommendationWarning, ...] # severity="error"，替代转移失败 warning
  warnings: tuple[RecommendationWarning, ...]    # 保留：非终止性提示（如 review 降权）
```

`unsupported` 字段删除——`detail_followup` 不再短路为 unsupported，改为 `detail_followup=True` 标记后由 `core/conversation.py` 接管执行。
`terminal_issues` 非空时 route 不可执行；它使用现有 response 可承载的 warning DTO，避免同时维护 `RecommendationError` 与 `RecommendationWarning` 两套终端序列化。

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
- `GENERATION_PARSE_ERROR = "generation_parse_error"`（§5 用）
- `NO_GROUNDED_OUTPUT = "no_grounded_output"`（§5 用）

### 3.5 core 与 dispatcher 编排调整

- `resolve_recommend_route` 返回 `terminal_issues` 非空 → dispatcher 立即返回 `ConversationDispatchResult(kind="error")`（不走 recall），保留此前 warning。
- `ConversationDispatcher.dispatch` 是 conversation 请求唯一公开分派入口；它完成 input validation → snapshot/profile pin → implicit classify（如需）→ resolved validation → route → recommend/detail 分支。
- `RecommendationCore.recommend` 保持独立可调用，只接受已 resolved 的 recommend-path context；传入待分类 implicit context 或 `detail_followup` 时返回结构化 `invalid_conversation_state`，不再返回 `unsupported_for_recommend_core`。
- 为保持单请求 snapshot 一致性，`RecommendationCore` 新增包内私有 `_recommend_pinned(request, vp, execution_ctx)`；dispatcher 与公开 `recommend` 都调用它。不得由 dispatcher 先 pin 后再调用一个会二次读取 snapshot 的公开入口。
- `RecommendResponse` 新增向后兼容字段 `generation_profile_version: str | None = None`。所有发生 query-understanding/implicit generation 的成功响应必须填真实 generation profile version；不得继续把 `ranking_profile_version` 误传给 `LLMGenerationPort.generate(... generation_profile_version=...)`。
- direct `recommend` 中 `conversation_context=None` 保持 `new_search` 语义；只有 conversation dispatcher 把“三个 intent 字段全空”解释为待分类 implicit follow-up。
- 现有 `intent="bogus"` fallback 行为改为 `INVALID_INTENT` error（不再静默 fallback new_search）。

### 3.6 对 R3 现有测试的影响

`test_recommend_intent.py` 中四个测试断言会变，改为断言 error response：
- `test_more_mentors_without_prior_falls_back_with_warning`
- `test_same_field_without_anchor_falls_back_with_warning`
- `test_invalid_intent_falls_back_to_new_search_with_warning`
- `test_detail_followup_is_unsupported`（替换为 dispatcher detail 分派 + direct recommend 拒绝未解析 conversation context）

这是有意的语义收紧，按 R5 §4 "返回结构化错误而非猜测 intent"。

## 4. implicit intent LLM 分类器

自由文本追问 → `{intent, confidence, rationale}`，走共享 `ConstrainedGenerationPipeline`；业务层不直接调用 raw `LLMGenerationPort`。

### 4.1 入口判定

input-phase context 为 `intent_source="implicit", intent=None, intent_confidence=None`，或三者全空（应用层未声明意图的自由文本追问）时，由 `ConversationDispatcher._classify_implicit` 接管。`intent_source="explicit"` 不进分类器（§3 直接路由）。分类结果只能由 dispatcher 写回，外部调用方不得预填 implicit intent/confidence。

### 4.2 LLM 调用契约

```text
operation_id = "implicit_intent"
system_prompt_id = generation_profile.operations[operation_id].system_prompt_id
user_inputs = {
    "query_text": <脱敏后的追问文本, 长度上限按 query_max_chars>,
    "conversation_summary": <脱敏后的会话摘要, 不含完整证据包/用户档案原值>,
    "has_anchor": <bool, anchor_entity_id 是否存在>,
    "has_prior_results": <bool, prior_result_entity_ids 是否非空>,
}
fact_bundle = FactBundle(build_id=snapshot.build_id,
                         subject_id="implicit-intent", facts=(), source_refs=())
student_context = None                       # implicit 分类不消费用户档案
json_schema = generation_profile.operations[operation_id].json_schema  # 内容固定为：
{
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "confidence", "rationale"],
    "properties": {
        "intent": {"enum": ["new_search","more_mentors","same_field","refine_direction","detail_followup"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string", "maxLength": 200}
    }
}
generation_profile_version = generation_profile.version
```

`FactBundle` 构造器字段无默认值但接受空集合（`tuple(x) if x is not None else ()`），R5 在 `core/conversation.py` 直接构造空 bundle：`FactBundle(build_id=snapshot.build_id, subject_id="implicit-intent", facts=(), source_refs=())`。grounded 契约已允许空 bundle（§4 "可为空"）。

分类调用通过 `ConstrainedGenerationPipeline.generate(...)`。该 operation 的 FactBundle 为空，因此结果不得产生 `fact` claim；pipeline 仍负责输入内容政策审查、provider 调用、JSON schema parse 与 SafetyGuard 输出审查。`CitationValidator` 对空 claims 是 no-op，分类器不会伪装成 fact-based generation。

### 4.3 分类结果处理（`ConversationDispatcher._classify_implicit`，async）

| 情况 | 行为 |
|---|---|
| LLM 返回合法 JSON，`intent` 在白名单，`confidence >= threshold` | 用 `dataclasses.replace` 写回 `intent_source="implicit"`、`intent`、`confidence`，执行 resolved-phase validation，再转 §3 路由 |
| `confidence < threshold`（版本化阈值，默认 `0.6`） | 结构化错误 `needs_clarification` (warning)，不触发宽召回 |
| `intent` 枚举非法 / JSON 无法解析 | 结构化错误 `needs_clarification` (warning) |
| 输入或输出命中内容政策 | 结构化错误 `content_policy_refusal` (error)，不触发宽召回或 fallback |
| 状态转移校验失败（如分类出 `more_mentors` 但缺 prior） | 结构化错误（按 §3.2 规则，`more_mentors_requires_prior` 等） |
| LLM provider 不可用 / 超时 | 结构化错误 `intent_classification_unavailable` (error) |

### 4.4 不变量

- implicit 分类的 `rationale` **不进入** `RecommendResponse`（避免泄露 LLM 内部推理给 App 侧），只用于评测日志（按 overview §18 脱敏）。
- `confidence` 写入 `ConversationContext.intent_confidence`，随上下文消费，不出现在推荐卡片字段。
- 阈值 `implicit_intent_confidence_threshold`（默认 0.6）、prompt_id、JSON schema、summary/query 上限和 token budget 都来自 checked-in generation profile 并进入 `generation_profile_version`，不在 handler 改常数。

### 4.5 与 §3 的衔接

implicit 分类成功后，得到的 `intent` 走与 explicit 相同的状态转移校验（§3.2 表格），统一从 `resolve_recommend_route` 出口走。区别仅在 `intent_source` 标记（`implicit`）和 `intent_confidence` 是否填充。

### 4.6 `ConversationDispatchResult`（统一对外分派结果）

`ConversationDispatcher` 构造器固定为：

```text
ConversationDispatcher(core: RecommendationCore,
                       pipeline: ConstrainedGenerationPipeline,
                       settings: RecommendSettings)
```

dispatcher 通过 `core.deps` 消费 snapshot/facts/generation-profile/conversation-store ports，不复制 dependency container；实例只保存 immutable deps/settings，不保存 request/session mutable state。

```text
ConversationDispatchResult
  kind: "recommendation|detail_followup|clarification|error"
  context: ConversationContext | None       # invalid input 时可为空；成功时为 resolved context
  recommendation: RecommendResponse | None
  detail_followup: DetailFollowupResponse | None
  issues: tuple[RecommendationWarning, ...]
  generation_profile_version: str | None
```

不变量：

- `kind=recommendation` 时仅 `recommendation` 非空；`kind=detail_followup` 时仅 `detail_followup` 非空。
- `kind=clarification` 时两个 payload 都为空，且 `issues` 含 `needs_clarification` warning；它不触发 recall。
- `kind=error` 时两个 payload 都为空，且 `issues` 至少含一个 `severity="error"` 项。
- HTTP adapter 只按 `kind` 映射 payload，不在 handler 重新分类 intent。

## 5. detail_followup 受约束生成

R5 第一个推荐域 fact-based 受约束生成消费者。`detail_followup` 路由由 §3 校验通过后（`anchor_entity_id` 非空），由 `ConversationDispatcher._resolve_detail_followup_pinned` 接管，**不进入** recall/rerank pipeline。

### 5.1 执行流程（`_resolve_detail_followup_pinned`，async）

```text
1. 复用 dispatcher 已 pin 的 snapshot + generation profile；本函数不得再次读取 ACTIVE pointer/profile
2. anchor detail = await facts_port.get_detail(
       snapshot, ctx.anchor_entity_id,
       include_contacts=False,
       viewer_permissions=vp,
    )                                              # 复用 R4b adapter
    - ProfessorFactNotFound → 结构化错误 anchor_not_in_active_build (error)
    - review entity 且 vp.can_view_review=False → 同样映射 anchor_not_in_active_build（不泄露 review entity 的存在）
3. fact_bundle = anchor_detail.fact_bundle         # R4b 已组装, contacts 已剥离
4. result = await generation_pipeline.generate(
       operation_id="detail_followup",
       system_prompt_id=generation_profile.operations["detail_followup"].system_prompt_id,
       user_inputs={"question": <脱敏追问>, "display_name": anchor_detail.display_name},
       fact_bundle=fact_bundle,
       student_context=request.student_context,    # 可为空
       json_schema=generation_profile.operations["detail_followup"].json_schema,
       generation_profile_version=generation_profile.version,
       safety_domain="recommend",
       include_contacts=False,
    )                                              # pipeline 内按固定顺序做 parse/support/citation/safety
5. 解析受约束 output，组装 DetailFollowupResponse
```

`ConstrainedGenerationPipeline` 落在共享 `src/dext_grounded/pipeline.py` 并从 `dext_grounded` 顶层 re-export，是唯一 caller-facing 保证：

```text
SafetyGuard.inspect_input (before provider call)
  -> raw LLMGenerationPort.generate
  -> JSON/schema parse
  -> operation-specific support-map validation
  -> CitationValidator.validate
  -> SafetyGuard.inspect
  -> validated GenerationResult
```

为消除当前 `dext_grounded/ports.py` docstring 与实际 fake/消费者行为的冲突，R5 落地前先修订共享契约与 contract tests：`LLMGenerationPort.generate` 返回已包装为 `GenerationResult`、但尚未经过 citation/safety 的 provider 结果；只有 `ConstrainedGenerationPipeline.generate` 可向业务层返回最终结果。pipeline 接受可选的 operation-specific support validator callback。业务代码不得在 pipeline 外再次调用 CitationValidator 造成双重 warning。

detail operation 使用固定 JSON schema，不允许 `json_schema=None`：

```text
{
  "answer": str,
  "claims": [{
    "text": str,
    "content_class": "fact|advice|uncertain",
    "fact_indices": list[int],
    "fact_refs": list[SourceRef]
  }]
}
```

对每个 `fact` claim，`fact_indices` 必须非空且索引合法；其 `fact_refs` 必须是对应 `FactItem.source_refs` 并集的非空子集。`advice` 可不带 fact；`uncertain` 不得带 fact refs。该 support-map 检查补足 CitationValidator 只能验证“引用存在于 bundle”、不能验证“引用属于所声明事实”的缺口。它不宣称替代语义 entailment；语义支持度继续进入 groundedness eval。

### 5.2 `DetailFollowupResponse`（新增 model，不复用 `RecommendResponse`）

```text
DetailFollowupResponse
  build_id: str
  ranking_profile_version: str
  generation_profile_version: str
  grounded_rules_manifest_hash: str
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

- `answer` 来自 JSON output 的 `answer` 字段，禁止把整个 dict 隐式 `str(...)` 后返回。
- 每条 `fact` claim 先通过 fact-index support-map，再由 CitationValidator 将 refs canonicalize；合法 refs 必在对应 FactItem 的 refs 内。
- `answer` 不得含 anchor_detail.contacts 字段。followup generation 无论调用方是否有 detail 联系方式权限，都以 `include_contacts=False` 读取 facts 并执行 SafetyGuard；查看联系方式必须走非生成式 professor detail endpoint。
- FactItem 缺少支持或 support-map 不合法时，claim 降级 `uncertain` 或删除；不得借用同一 bundle 内无关引用伪装 grounded。
- snapshot pinned 一次，`get_detail` 与 `generate` 共用同一 snapshot（overview §9 一致性）。

### 5.4 失败模式（grounded §8 + R5 §7）

- LLM provider 不可用 → `followup_generation_unavailable` (error)
- JSON 无法解析 → `generation_parse_error` (error)
- 引用校验全部失败 → `no_grounded_output` (error)，不返回无引用纯 LLM 文本
- 事实包为空却要求 fact 输出 → `insufficient_facts` (warning/error，按 grounded §8）
- 输入或输出命中内容政策 → `content_policy_refusal` (error)，不返回 answer/claims/cited refs 中的被拒绝文本

pipeline 的 `GenerationWarning` 不直接塞入 recommendation response；dispatcher 通过固定 mapping 转为 `RecommendationWarning`。其中 `json_parse_failed/schema_validation_failed` → `generation_parse_error`，`no_grounded_output` → 同名 terminal error，`content_policy_refusal` 及分类码（`mentor_attack` 等）→ `content_policy_refusal` terminal error，`unauthorized_contact/no_probability_claim` 保留 warning code 与清洗后的 output。禁止按 message 文本分类。

### 5.5 与 R6 的复用

R5/R6 都复用共享 `ConstrainedGenerationPipeline`。R5 在共同开工前冻结 raw-port/pipeline ownership 与 generation profile schema；之后两阶段可分别增加 operation 配置、`user_inputs` 组装和输出 adapter，不互相等待业务实现，也不得各写一套 citation/safety 顺序。

### 5.6 `RecommendationCore` 接口

新增 `ConversationDispatcher.dispatch(request, *, viewer_permissions, conversation_summary=None) -> ConversationDispatchResult` 作为 conversation 唯一公开入口。
dispatcher 在内部调用 `_recommend_pinned` 或 `_resolve_detail_followup_pinned`；HTTP 层不按未解析 intent 预选方法。
`RecommendationCore.recommend(...) -> RecommendResponse` 保持单一返回类型，供明确的非 conversation 推荐调用；它不承担 implicit classify/detail dispatch。

## 6. ConversationStorePort + fake

§6 要求 R5 定义 store-neutral repository port 与 fake；真实 PostgreSQL schema/repository 归 R7b。R5 只固化会话/fork 关系的最小字段，不存完整证据包与用户档案（§6 "写历史时只保存 response snapshot 所需的最小字段"）。

### 6.1 端口定义（`ports/conversation_store.py`，新增）

```text
@runtime_checkable
class ConversationStorePort(Protocol):
    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None
    async def load_summary(self, session_id: str, through_turn_id: str | None) -> ConversationSummary | None
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
  generation_profile_version: str | None
  result_entity_ids: tuple[str, ...]      # 仅 entity ID, 不含完整卡片/证据
  intent: str | None
  intent_source: str | None
  sanitized_summary: str | None           # 受 summary_max_chars 限制的脱敏摘要；非原始 query/messages
  created_at: str                          # ISO-8601
```

### 6.3 边界约束

- `TurnSnapshot` **不含** `FactBundle`、`SourceRef`、`StudentContext`、完整 query_text/messages、联系方式（overview §6/§18 隐私）。`sanitized_summary` 只允许 §2.5 的受限内容。证据需重读时从 ACTIVE build 重新组装，不历史落库。
- `ConversationContext` 经 store 往返必须保持字段一致（`main_session_id`/`source_turn_id` 表达 fork）。
- store **不持有** fork 树结构——`resolve_fork` 仅按 `(main_session_id, source_turn_id)` 取回源 turn 上下文，不递归遍历。
- 推荐核心通过 store **只读消费**上下文；`save_turn` 由应用层（HTTP adapter）在响应后调用，core 自身不写 store（保 §6 "推荐核心不保存 session、不写历史"）。

### 6.4 `FakeConversationStorePort`（`ports/_fakes.py`，新增）

```text
FakeConversationStorePort
  __init__(initial: dict[tuple[str, str|None], ConversationContext] | None = None)
  load_context(session_id, turn_id) -> ConversationContext | None
  load_summary(session_id, through_turn_id) -> ConversationSummary | None
  save_turn(session_id, turn_id, context, snapshot) -> None  # 写入内存 dict
  list_prior_entity_ids(session_id, limit) -> tuple[str, ...]
  resolve_fork(main_session_id, source_turn_id) -> ConversationContext | None
  saved_turns: list[dict]   # 测试断言用
```

### 6.5 R5 范围内 store 的使用

- `ConversationDispatcher` 在 implicit 分类前可 `load_context` 补齐前序上下文，并独立 `load_summary` 获取 §2.5 摘要；两者不得混为一个返回值。调用方已传 summary 时不访问 store summary。store 在 R5 主要用于 fake-driven 单测覆盖上下文/摘要往返一致性。
- R7b 才接 PostgreSQL；R5 的 `ConversationStorePort` 是契约占位 + fake，保证 R7b 只填实现不改接口。

### 6.6 Re-export

`ConversationStorePort`、`FakeConversationStorePort`、`ConversationSummary`、`TurnSnapshot` 加入 `dext_recommend/__init__.py` 的 `__all__`。

### 6.7 `RecommendDeps` 调整

`conversation_store` 字段保持可选——`RecommendDeps.conversation_store: ConversationStorePort | None = None`，dispatcher 内部判空跳过 load。避免强依赖 store 才能 recommend。

R5 同时新增 required `generation_profile_port: RecommendGenerationProfilePort`；生产实现从 `RecommendSettings.generation_profile_path` 加载 checked-in artifact，fake 用于单测。profile 至少包含：

```text
RecommendGenerationProfile
  version: str
  grounded_rules_manifest_hash: str
  operations:
    implicit_intent: {system_prompt_id, json_schema, timeout, token_budget,
                      confidence_threshold, query_max_chars, summary_max_chars}
    detail_followup: {system_prompt_id, json_schema, timeout, token_budget}
```

profile loader 必须拒绝缺 operation、空 prompt id、非法 schema、非正 timeout/token budget、阈值越界和 manifest hash 不匹配。R6 在同一 artifact 中新增 match/email/compare operation，不修改 R5 字段语义。

## 7. 验收与测试边界

按 R5 §7 验收标准 + R0/R1 接受门槛（单模块绿、不要求全项目；LLM 测试用真实 key）。

### 7.1 测试文件划分

| 文件 | 覆盖 |
|---|---|
| `test_recommend_conversation_context.py` | §2 `_validate_context`/`_assemble_context`：字段校验、session/turn 同进同出、fork 同进同出、prior 去重、不可变 |
| `test_recommend_intent.py`（改） | §3 严格状态转移：more_mentors 缺 prior→error、same_field/detail_followup 缺 anchor→error、invalid intent→error（替代旧 fallback 断言）；R3 排序语义不动 |
| `test_recommend_conversation_implicit.py` | §4 两阶段校验 + LLM 分类：未解析 implicit 可进入、外部预填被拒；高置信→统一 dispatch、低置信/非法枚举→clarification、状态转移失败→error、LLM 不可用→intent_classification_unavailable、内容政策→content_policy_refusal |
| `test_recommend_conversation_dispatch.py` | implicit 分类为 detail 时直接进入 detail branch；分类为 recommend intent 时进入 `_recommend_pinned`；两路 snapshot/profile 都只 pin 一次；HTTP 不参与 intent 分派 |
| `test_recommend_detail_followup.py` | §5 fact-based 生成：anchor 校验、FactBundle 注入、support-map 阻止无关合法引用、CitationValidator 剔除伪造引用、SafetyGuard 始终清洗 contacts/概率承诺并硬拒答内容政策、固定 JSON→answer 映射、ProfessorFactNotFound→error |
| `test_recommend_generation_profile.py` | checked-in profile/loader：required operations、prompt/schema/threshold/budget/manifest hash、版本进入 recommendation/detail/dispatch response |
| `test_recommend_conversation_store.py` | §6 context/summary 往返：load/save 一致性、summary 优先级与脱敏上限、list_prior、resolve_fork、TurnSnapshot 不含敏感字段 |
| `test_recommend_immutability.py`（改） | 新增 `DetailFollowupResponse`、`ConversationDispatchResult`、`ConversationSummary`、`TurnSnapshot` 深度不可变断言 |
| `test_recommend_import_boundary.py`（改） | `dext_recommend` 不 import `dext`/`dext_graph`/`dext_monitor`/`dext_competition`；`dext_grounded` 允许 |

### 7.2 LLM 测试边界（CLAUDE.md 测试策略 + R0/R1 门槛）

- implicit 分类器与 detail_followup 生成**都是 LLM-touching**；provider 行为/提示词质量 smoke 按 CLAUDE.md 使用真实 `DEEPSEEK_API_KEY`，不把 fake 结果当模型质量证明。
- `FakeLLMGenerationPort` 与 `FakeRecommendGenerationProfilePort` 是契约测试替身，用于验证 dispatch、错误码、support-map/CitationValidator/SafetyGuard 编排；这些纯逻辑测试必须离线稳定运行。
- 真实 LLM 只用于少量"端到端" smoke：implicit 分类准确率样本 + detail_followup groundedness 样本（overview §16 评测契约口径）。这些样本在缺少 `DEEPSEEK_API_KEY` 时 skip，单模块绿不要求必跑。

### 7.3 async 边界（R5 §7 最后一条）

- `ConversationDispatcher.dispatch`、`_classify_implicit`、`_resolve_detail_followup_pinned`、`ConversationStorePort.*` 为 `async def`。
- `_validate_context`、`_assemble_context`、`resolve_recommend_route`、support-map validator、`CitationValidator.validate`、`SafetyGuard.inspect_input`、`SafetyGuard.inspect` 保持同步。
- `SafetyGuard` 方法名固定为现有 `inspect`，不得在实现/测试中发明 `apply`。

### 7.4 import boundary

`core/conversation.py` 可 import `dext_grounded`（`ConstrainedGenerationPipeline` contract）、`core/intent.py`、`models.py`、`ports/`；**不** import `api/`、具体 adapter、`dext_graph`。

### 7.5 评测契约口径（R5 §7 + overview §16）

- `eval/` 落地 `implicit conversation routing accuracy` 与 `explicit route contract pass rate` 的**口径与样本形状**（不要求线上规模基线值，R7c 才跑全量）。
- 样本绑定 `generation_profile_version`、`build_id`、`commit hash`。

### 7.6 非目标（重申）

- PostgreSQL schema/repository（R7b）。
- match/email/compare 生成（R6）。
- 在线 LLM 事实生成（grounded §6 SafetyGuard 已拦）。
- production composition / HTTP OpenAPI 适配（R7a/R7b）。

## 8. TDD 落地顺序与文件所有权

1. **共享 seam**：新增 `src/dext_grounded/pipeline.py`、修订 `ports.py` docstring/contract tests，钉住 raw port 与 validated pipeline 的唯一边界。
2. **generation profile**：新增 `data/recommend/generation-profile.json`、recommend profile model/port/loader/fake；修正 query-understanding 不再借用 ranking version。
3. **models/context**：新增 `ConversationSummary`、`ConversationDispatchResult`、`DetailFollowupResponse` 与 response version 字段；先写 immutability/shape tests。
4. **strict route**：两阶段 context validation、`RecommendRoute.terminal_issues` 与 R3 fallback 测试迁移。
5. **dispatcher/implicit**：实现统一 dispatch、单次 snapshot/profile pin、clarification/error 分支。
6. **detail generation**：实现固定 JSON schema、support-map validator、generation-warning mapping、contacts fail-closed。
7. **store/eval**：补 ConversationStorePort/fake、summary round-trip、conversation eval 契约与少量 live smoke。

每一步先 RED 后 GREEN；步骤 1–2 是 R6 并行开工门禁。R6 可在步骤 2 合并后独立增加 operation 配置，不得同时改 `pipeline.py` 的调用顺序或 profile 根 schema。

## 9. 非目标

- R6 match/email/compare 生成（R5 只为 R6 验证管线可复用，不实现这三个能力）。
- PostgreSQL schema/repository（R7b）。
- 在线 LLM 事实生成、production composition、HTTP OpenAPI 适配。
- fork 树遍历、跨会话长期画像、点击反馈学习（overview §19 非目标）。
