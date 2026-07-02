# 阶段 6：dext_recommend auxiliary generation

> 状态：设计稿
>
> 前置依赖：[R4b professor facts](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md) 事实包 + [共享 grounded-generation](2026-06-30-dext-grounded-generation-design.md) 已实现
>
> 后续阶段：与 [Conversation adapter](2026-06-30-dext-recommendation-05-conversation-design.md) 可并行；共同进入 [R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md)

## 1. 目标

实现匹配分析、套磁邮件与导师对比的受约束生成。这三个能力是 App 导师闭环的一部分，但不是新事实来源——LLM 输出只能消费已选事实包与用户授权背景，禁止无来源事实与录取概率承诺。本阶段不实现 HTTP，只交付进程内服务接口。

## 2. 三个辅助服务

```text
async analyze_match(entity_id, student_context, evidence_policy) -> MatchAnalysis
async draft_outreach_email(entity_id, student_context, tone, language) -> OutreachDraft
async compare_professors(entity_ids[2..3], student_context, evidence_policy) -> ProfessorComparison
```

三个服务都走共享 `LLMGenerationPort`。服务入口固定一次 `ActiveBuildSnapshot`，通过 R4 读取
`ProfessorDetail`，只把 `ProfessorDetail.fact_bundle` 交给生成管线；展示 DTO 本身不是 `FactBundle`。

## 3. 匹配分析

- 输入为 `ProfessorDetail` 与 `StudentContext`。
- 输出维度可包括研究方向相关性、经历背景相关性、准备程度、信息完整度和建议下一步。
- 雷达图维度分数必须是解释性分数，**不是**招生预测。
- 明确禁止输出录取概率、保研概率、导师是否会接收等承诺——由共享 `SafetyGuard` 的 `no_probability_claim` 规则拦截。

## 4. 套磁邮件

- 输入为 `ProfessorDetail` 与用户可授权使用的个人背景。
- 输出为 `{subject, body}` 草稿，用户可编辑和复制。
- 不自动发送邮件，不伪造经历，不加入未授权联系方式。
- 邮件中涉及导师事实的内容必须来自事实包；联系方式默认不带入，`include_contacts` + 权限校验通过才可选附加。

## 5. 导师对比

- 输入 2-3 位 `entity_id` 与可选 `StudentContext`。
- 输出横向对比报告，覆盖研究方向、适合背景、潜在优势、准备建议和信息缺口。
- 每个结论必须能追溯到至少一个教师事实或用户背景字段；无法回溯的断言由 `CitationValidator` 降级或剔除。
- 某位导师证据不足时在对比中显式标注，不得用流畅文案掩盖缺口。

## 6. 受约束生成管线

每个服务统一走（由共享契约提供，本阶段只装配）：

```text
pin snapshot -> ProfessorDetail.fact_bundle -> FactBundle trimming -> await LLMGenerationPort.generate
  -> CitationValidator -> SafetyGuard -> GenerationResult
```

- 事实裁剪按 token 预算保留命中字段，超长 ResearchStatement/论文摘要截断后保留可回溯 `SourceRef`。
- 引用校验：断言无对应引用降级 `uncertain` + `uncited_claim`；自报引用不在事实包集合内标记 `fabricated_ref` 并剔除。
- 安全边界：概率承诺、无来源事实、伪造引用、违规建议、越权联系方式由共享规则拦截（见 grounded-generation §6）。

## 7. 版本化与评测

- 所有 prompt、JSON schema、裁剪阈值进入 `generation_profile_version`，与 `ranking_profile_version` 平级。
- 评测样本按 overview §16：匹配分析、套磁邮件、导师对比的 groundedness 样本 + no-admission-probability 样本，绑定 `generation_profile_version`。
- 指标：`grounded generation precision`、`no-admission-probability rate`。

## 8. 失败模式

- LLM provider 不可用：返回 `generation_unavailable`，不静默回退。
- 输出无法解析为声明 JSON schema：返回 `generation_parse_error`，不交付半结构化结果。
- 事实包为空却要求 `fact` 输出：返回 `insufficient_facts`。
- 引用校验全部失败：返回 `no_grounded_output`，不返回无引用纯 LLM 文本。
- 缺少用户授权背景字段：返回 `insufficient_student_context` warning，不编造背景。

## 9. 验收标准

- 三个服务都通过共享 `LLMGenerationPort`/`CitationValidator`/`SafetyGuard`，不在 handler 中内联 prompt 或安全规则。
- 匹配分析不输出录取/保研/导师接收意愿；套磁邮件不自动发送、不伪造经历、不越权联系方式；对比结论可回溯。
- 评测样本中 `grounded generation precision` 与 `no-admission-probability rate` 同时达标，样本绑定 `generation_profile_version`。
- 失败模式返回对应结构化错误，不静默回退或交付无引用输出。
- 单测可用 fixture 事实包 + fake `LLMGenerationPort` 覆盖受约束生成逻辑（grounded 逻辑部分不调真实 LLM）。
- 三个服务及 fake LLM port 均为 async；引用校验、安全检查和事实裁剪保持同步纯计算。
