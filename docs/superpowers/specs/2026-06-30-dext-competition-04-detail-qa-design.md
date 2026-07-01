# 阶段 4：dext_competition detail and grounded QA

> 状态：设计稿
>
> 前置依赖：[阶段 2 competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md)目录 + [阶段 1 knowledge index](2026-06-30-dext-competition-01-knowledge-index-design.md)索引可用 + [共享 grounded-generation](2026-06-30-dext-grounded-generation-design.md)
>
> 后续阶段：[Plan generator](2026-06-30-dext-competition-05-plan-generator-design.md)

## 1. 目标

实现竞赛详情与规则问答。后端内部强制 source refs 与时效/复核 warning，LLM 回答只能基于知识库片段，不编造赛事规则；公开 API 默认不暴露文件路径或 chunk hash。本阶段不实现备赛计划（阶段 5）。

## 2. 接口

按 overview §3 辅助能力：

```text
get_competition_detail(competition_id) -> CompetitionDetail
answer_competition_question(question, competition_id?, context?) -> GroundedAnswer
compare_competitions(competition_ids[2..4], student_context?) -> CompetitionComparison
```

三个接口都走共享 `LLMGenerationPort`，传入阶段 2 的 `CompetitionCard`（映射为共享 `FactBundle`）作为 `fact_bundle`。

## 3. CompetitionDetail

```text
CompetitionDetail
  competition_id
  display_name
  category
  rules_summary            # 规则摘要
  eligibility
  schedule
  team_policy
  materials
  ai_compliance
  preparation_focus
  official_links
  internal_source_refs: list[SourceRef]
  risk_flags
  freshness_warnings       # 时效复核提示
  available_actions: detail|create_plan|ask_rules|compare
```

每条规则摘要在后端内部附 `SourceRef`；时效性字段附 `freshness_warnings` 提示用户回到当届官方通知与本校文件复核。公开 API 默认只返回产品化的规则摘要、限制和复核提示。

## 4. 规则问答

`answer_competition_question` 流程：

1. 从 query 与 `competition_id`（可选）检索阶段 1 索引的相关 chunk。
2. 把 chunk 映射为共享 `FactBundle`。
3. 调用共享 `LLMGenerationPort` 生成回答。
4. `CitationValidator` 逐 `Claim` 校验：`fact` 类断言映射回 `SourceRef`，`advice` 引用用户背景时映射回 `UserContextRef`，无法回溯的断言降级 `uncertain` 或剔除。
5. `SafetyGuard` 拦截概率承诺、违规建议、把往届信息当当届事实等。

规则问答覆盖范围（overview §8 评测）：资格、组队、AI 使用、时间、提交物、校内认定和常见混淆，共 20 条样本。

## 5. 时效与复核

- 具体报名日期、赛道、资格和费用具有时效性。除知识库明确写明年份和来源外，回答必须提示复核（README 数据口径与信息核验顺序）。
- `stale_fact` warning：把往届时间、奖项比例、赛道、费用、AI 规则当成当届确定事实时降级 `uncertain`。
- 信息核验顺序：当届官网 → 主办单位 → 省级赛区/承办高校/本校教务处 → 聚合平台只用于发现线索。

## 6. 竞赛对比

`compare_competitions` 输入 2-4 位 `competition_id` 与可选 `StudentContext`，输出横向对比报告。每个结论可回溯到至少一个赛事事实或用户背景字段；某赛事证据不足时显式标注，不用流畅文案掩盖缺口。

## 7. 安全边界

走共享 `SafetyGuard` 竞赛侧规则：禁止承诺获奖/保研/Offer 概率；禁止把 2024 目录写成“教育部白名单”；当用户目标是校内认定/经费/综测加分时把“查本校文件”作为必要行动项；拒绝代做/挂名/伪造数据/泄题/绕查重/规避 AI 披露。

## 8. 验收标准

- `CompetitionDetail` 字段覆盖 §3，每条规则摘要后端内部附 `SourceRef`。
- 规则问答每条断言可回溯 `SourceRef`；无法回溯的断言由 `CitationValidator` 降级 `uncertain` 或剔除。
- 时效性字段附 `freshness_warnings`；往届信息当当届事实时降级 `uncertain`。
- 评测样本覆盖 20 条规则问答，`groundedness precision` 与 `rule freshness warning rate` 达标。
- 安全边界由共享 `SafetyGuard` 拦截违规建议与概率承诺。
- `CompetitionCard` 可映射为共享 `FactBundle` 供受约束生成消费。
