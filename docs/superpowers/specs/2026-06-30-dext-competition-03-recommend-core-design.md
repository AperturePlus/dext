# 阶段 3：dext_competition recommend core

> 状态：设计稿
>
> 前置依赖：[阶段 2 competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md)目录可用 + [共享 grounded-generation](2026-06-30-dext-grounded-generation-design.md)
>
> 后续阶段：[Detail and grounded QA](2026-06-30-dext-competition-04-detail-qa-design.md)

## 1. 目标

实现 `CompetitionRecommendRequest -> CompetitionRecommendResponse` 全链路：需求理解、候选召回、排序、解释与风险提示。本阶段不实现详情与规则问答（阶段 4）、备赛计划（阶段 5）与计划助手（阶段 6）。

## 2. 请求与响应

按 overview §3 固化：

```text
CompetitionRecommendRequest
  query_text: str
  student_context: StudentContext | null
  preferences: CompetitionPreferences
  limit: int = 6
  diagnostics_level: "none|summary|debug" = "summary"

CompetitionPreferences
  categories: list[str] = []
  major: str | null
  grade: str | null
  experience_level: "beginner|intermediate|advanced" | null
  weekly_hours: int | null
  target_goal: "learn|portfolio|school_recognition|research|job_skill" | null
  team_preference: "solo|team|either" | null
  time_window: str | null
  risk_tolerance: "low|medium|high" = "medium"

CompetitionRecommendResponse
  knowledge_base_version: str
  query_understanding: CompetitionQueryUnderstanding
  results: list[RecommendedCompetition]
  warnings: list[CompetitionWarning]
```

`StudentContext` 从共享契约 import，不重复定义。

## 3. 需求理解

`CompetitionQueryUnderstanding` 输出：

```text
CompetitionQueryUnderstanding
  interests
  major_fit
  grade_fit
  experience_level
  weekly_hours
  target_goal
  team_preference
  missing_information
  needs_clarification
```

需求理解调用共享 `LLMGenerationPort`（轻量），输出映射回结构化字段；无法解析时返回 `needs_clarification=true`，不直接触发推荐。

## 4. 候选召回与过滤

- 从阶段 2 目录召回，v1 用 BM25/关键词匹配 + 结构化过滤（category、资格、组队偏好、时间窗口）。
- 召回基于阶段 1 索引，每条候选附 `SourceRef`。
- 召回不足时不从训练记忆补编赛事，返回结构化 warning 并提示用户补充 query。
- 目录内外赛事都可推荐，但必须标明 `in_2024_catalog` 与“学校认定需另查本校文件”。

## 5. 排序

score components 按 overview §5 表的初始权重：

| 组件 | 初始权重 |
|---|---:|
| interest_match_score | 0.30 |
| eligibility_score | 0.20 |
| effort_fit_score | 0.18 |
| timeline_fit_score | 0.12 |
| goal_fit_score | 0.10 |
| provenance_score | 0.10 |

权重、阈值与 tie-break 必须进入 `competition_ranking_profile_version`，不在 handler 中改常数。不使用“含金量榜单”“获奖概率”“保研加分概率”作为排序事实。

`fit_level` 按版本化阈值从 score components 派生，面向卡片展示。

## 6. 推荐卡片与解释

`RecommendedCompetition` 按 overview §3 最小语义：

```text
competition_id
display_name
category
summary
fit_level
score
score_components
eligibility_notes
schedule_notes
team_notes
preparation_effort
short_reasons
risk_flags
official_links
source_refs
available_actions: detail|create_plan|ask_rules|compare
```

- 每条推荐至少 1 个 `SourceRef`；确实缺来源时显式标注 `uncertain`。
- 推荐理由区分 `fact`/`advice`/`uncertain` 三类（共享 `ContentClass`）。
- 时效性字段（报名日期、赛道、费用、AI 规则）附复核提示，不当届确定事实。

## 7. 安全边界

走共享 `SafetyGuard`：禁止承诺获奖概率、保研/奖学金/综测收益或企业 Offer；禁止把 2024 目录写成“教育部白名单”；禁止把往届信息当当届确定事实；拒绝代做、挂名、伪造数据、赛中泄题、绕过查重、规避 AI 披露等违规建议。

## 8. 验收标准

- `CompetitionRecommendRequest -> CompetitionRecommendResponse` 全链路可用。
- 排序权重配置化并进入 `competition_ranking_profile_version`；无“含金量/获奖概率/保研加分”作为排序事实。
- 每条推荐至少 1 个 `SourceRef`；缺来源显式标注 `uncertain`。
- 时效性字段附复核提示；`in_2024_catalog` 不被写成“教育部白名单”。
- 安全边界由共享 `SafetyGuard` 拦截违规建议。
- 评测样本覆盖 overview §8：30-50 条竞赛推荐 query，跨计算机/电子信息/数学建模/机器人/工学/经管/综合创业/语言艺术/医学生命科学。
