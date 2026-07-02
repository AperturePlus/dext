# dext AI 回复防御性内容安全与导师保护

> 状态：设计稿 + 首版实现目标
>
> 前置依赖：[受约束生成与事实引用](2026-06-30-dext-grounded-generation-design.md)
>
> 范围：所有用户可见 AI 回复，包括推荐对话、详情追问、匹配分析、套磁邮件、导师对比和竞赛生成。

## 1. 目标

在现有 grounded-generation 的事实约束、引用校验和 SafetyGuard 后处理基础上，新增内容政策审查。AI 不得生成或回答政治敏感、人身攻击、色情、暴力、攻击导师等负面内容。命中时必须硬拒答，并给出中性的替代方向，例如基于公开事实讨论研究方向、招生信息、沟通策略或学习规划。

## 2. 内容政策

`dext_grounded` 的 `grounded_v1.yaml` 维护版本化 `content_policy`：

- `political_sensitive`：政治敏感、煽动性或政治攻击内容。
- `personal_attack`：侮辱、贬损、嘲讽、人格攻击。
- `sexual_content`：色情、裸露、性行为或性暗示生成。
- `violent_content`：暴力伤害、血腥或攻击指令。
- `mentor_attack`：针对导师/教授/老师的人身攻击、侮辱、贬损、无来源指控或引导攻击。

每个分类的规则、拒答模板和替代建议都进入 grounded rules manifest hash；推荐侧 generation profile 必须绑定该 hash。

## 3. 审查链路

所有用户可见生成必须经过同一链路：

```text
input policy review -> raw LLM generation -> support validator -> CitationValidator -> SafetyGuard.inspect -> user-visible response
```

- 输入审查：在调用 LLM 前检查用户 query、对话摘要、用户提供草稿等文本；命中即不调用 LLM。
- 输出审查：对 `claims` 与 `output` 同步检查；命中即 `claims=[]`，并返回 schema 兼容的拒答对象或固定拒答文本。
- 日志只允许记录分类、operation、action、request id、profile version；不得记录敏感原文或攻击性文本。

## 4. 导师保护

禁止输出“导师垃圾/人品差/不行/避雷”等攻击性结论。允许输出中性、可证据化表述：

- 研究方向与用户兴趣不匹配。
- 公开主页未提供足够信息。
- 招生资格或当年招生信息未核验。
- 建议换用客观筛选条件，或采用礼貌沟通措辞。

导师对比只能比较事实维度、证据缺口和适配度，不得扩展为人格评价或攻击性推荐。

## 5. 失败与映射

命中内容政策时，grounded 层返回 `content_policy_refusal`，并附带分类 code（如 `mentor_attack`）。推荐/HTTP 层映射为 severity=`error` 的用户可见拒答；不得降级为普通 warning，不得交付半清洗输出。

推荐系统落点：

- R3 `RecommendationCore.recommend`：在 snapshot/profile/LLM/vector/facts 前对 `query_text` 做 `SafetyGuard.inspect_input`，命中零外部调用返回 `content_policy_refusal`。
- R5 `ConversationDispatcher`：implicit intent 与 detail follow-up 复用 `ConstrainedGenerationPipeline`，命中内容政策返回 terminal error，不转成 `needs_clarification`。
- R6 auxiliary generation：match-analysis、outreach-email、professor-comparison 都把内容政策 warning 映射为 terminal error。
- R7 HTTP/runtime/acceptance：只映射结构化 code，不重新实现规则；日志、history、trace 不保存被拒绝原文；startup 校验 generation profile 的 grounded rules manifest hash。

## 6. 验收

- `SafetyGuard.inspect_input` 在 LLM 前拒绝敏感输入，且 warning 不包含敏感原文。
- `SafetyGuard.inspect` 对输出中的敏感内容硬拒答，并同时清空 claims。
- 详情追问、匹配分析、套磁邮件、导师对比均复用 pipeline，不在业务 handler 中内联安全规则。
- `generation-profile.json` 的 `grounded_rules_manifest_hash` 与新增规则一致。
