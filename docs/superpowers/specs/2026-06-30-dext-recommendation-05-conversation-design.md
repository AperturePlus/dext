# 阶段 5：dext_recommend conversation adapter

> 状态：设计稿
>
> 前置依赖：[R4b professor facts](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md) 事实包稳定
>
> 后续阶段：与 [Auxiliary generation](2026-06-30-dext-recommendation-06-auxiliary-generation-design.md) 可并行；共同进入 [R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md)

## 1. 目标

实现 session/turn/fork 上下文校验、implicit intent 分类与 `ConversationContext` 组装。推荐核心保持无状态、
可独立调用；R3 继续拥有五种 intent 的推荐执行语义，R5 不复制排序、过滤、same-field 或 oversample 逻辑。
本阶段不实现 PostgreSQL 持久化，也不实现匹配/套磁/对比生成。

## 2. ConversationContext

固化 overview §8 的会话语义：

```text
ConversationContext
  session_id: str | null
  turn_id: str | null
  main_session_id: str | null          # fork 关系
  source_turn_id: str | null
  anchor_entity_id: str | null          # 锚定导师
  intent: "new_search|more_mentors|same_field|refine_direction|detail_followup" | null
  intent_source: "explicit|implicit" | null
  intent_confidence: float | null
  prior_result_entity_ids: list[str]
```

`main_session_id`、`source_turn_id` 由应用层保存以表达 fork 关系，推荐核心只消费、不持久化。

## 3. intent 路由

本表描述最终行为，但推荐执行仍委托 R3 core。R5 的输出是已校验的 route/context；除 `detail_followup`
交给事实/生成服务外，不建立第二套推荐 pipeline。

按 overview §10 的路由语义表执行：

| intent | 后端行为 |
|---|---|
| `new_search` | 按当前 query 和用户背景生成新推荐 |
| `more_mentors` | 同一理解和过滤下补充候选，默认排除已返回 entity IDs |
| `same_field` | 以锚定导师或上一轮 Topic 为正向上下文，找同领域导师 |
| `refine_direction` | 合并用户新限制后重新推荐 |
| `detail_followup` | 以锚定导师事实包回答，不触发宽召回，除非显式要求更多导师 |

## 4. explicit vs implicit intent

intent 来源必须显式区分（overview §10）：

- `explicit`：来自 App 按钮、菜单或枚举动作。后端只做枚举校验、状态转移校验和上下文完整性校验；校验通过后按枚举执行，不让 LLM 重新解释。
- `implicit`：来自自由文本追问。后端用轻量约束分类器或 LLM JSON 输出 `{intent, confidence, rationale}`，只接受白名单枚举；无法解析、枚举非法、缺少必要上下文或置信度低于版本化阈值时返回 `needs_clarification`，不直接触发宽召回。

状态转移校验：例如 `detail_followup` 必须有 `anchor_entity_id`；`more_mentors` 必须有 `prior_result_entity_ids`；缺失时返回结构化错误而非猜测 intent。

## 5. fork 式追问

- `anchor_entity_id` 必须是 ACTIVE build 中存在且可解释的教师（通过 `await ProfessorFactPort.get_detail(...)` 校验）。
- fork 会话不得污染主会话结果集；应用层保存 `main_session_id`/`source_turn_id`，推荐核心只消费上下文。
- 细节追问基于 `ProfessorDetail` 事实包与证据片段回答，LLM 不得编造导师事实（受约束生成走共享契约）。
- 用户从锚定导师转为“找类似导师”：显式按钮直接传 `same_field`/`refine_direction`；自由文本先过 implicit 分类，低置信时追问澄清。

## 6. 应用层边界

推荐核心不保存 session、不写历史、不持有 fork 树。应用层负责：

- 持久化 session/turn/fork 关系与历史。
- 传入脱敏后的 `ConversationContext` 与必要 `StudentContext`。
- 写历史时只保存 response snapshot 所需的最小字段，避免把完整证据包与用户档案重复落库。

R5 只定义 store-neutral repository port 与 fake；真实 PostgreSQL schema/repository 归 R7b。
query-understanding/implicit intent 的 production LLM adapter 在 R5 落地；R6 generation 必须使用独立配置、
timeout、连接池与 generation profile，不能复用请求级 mutable client state。

## 7. 验收标准

- `ConversationContext` 字段与 overview §8 一致；推荐核心无状态，可被多次独立调用。
- 五种 intent 各自按路由语义表执行；`explicit` intent 不触发 LLM 重新解释，只做枚举与状态转移校验。
- `implicit` intent 解析失败、枚举非法、缺上下文、低置信时返回 `needs_clarification`，不触发宽召回。
- fork 会话不污染主会话结果集；`anchor_entity_id` 不在 ACTIVE build 时返回结构化错误。
- `implicit conversation routing accuracy` 与 `explicit route contract pass rate` 指标可评测，评测样本按 overview §16 拆分。
- 单测可用 fake ports 覆盖 intent 路由逻辑，不依赖真实外部服务。
- 会触发 LLM、推荐核心或 ProfessorFactPort 的 conversation 路由入口为 async；纯状态转移校验保持同步。
