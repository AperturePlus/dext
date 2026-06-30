# dext 竞赛助手后端设计

> 状态：设计稿（已拆为 overview + 7 个子 spec）
>
> 日期：2026-06-30
>
> 设计版本：competition-assistant-design-v1.0-knowledge-base
>
> 目标模块：`dext_competition`，与 `dext_recommend` 平级
>
> 前置产物：`data/竞赛助手/` Markdown 知识库已存在，覆盖竞赛索引、方向规则、备赛流程、FAQ、工具与答辩问题库
>
> 本文是 `dext_competition` 的 **overview spec**：只定义跨子 spec 的产品目标、模块边界、数据口径、排序约束与依赖序。每个落地步骤的详细数据结构、算法、接口与验收标准见对应子 spec。共享的受约束生成与事实引用契约见 [dext-grounded-generation](2026-06-30-dext-grounded-generation-design.md)。

## 1. 产品目标与范围

竞赛助手是 SchoNavi 的“选竞赛 -> 看规则 -> 生成备赛计划 -> 调整日历”后端能力层。它不属于导师推荐模块，也不读取教师 catalog、Neo4j 或 Qdrant 教师索引。

v1 目标：

1. 用户用自然语言描述专业、年级、兴趣、基础、时间投入和目标能力。
2. 系统推荐少量适合的竞赛，并给出可追溯理由和风险提示。
3. 竞赛详情能展示规则摘要、资格、赛制、组队、材料、时间窗口、官方入口和核验要求。
4. 用户选定竞赛后生成可执行备赛计划，支持提交型和窗口型时间模型。
5. 备赛 AI 助手只能提出计划改动卡，用户确认后才落地。

v1 优先保证事实可靠、规则边界清楚、推荐可解释和计划可执行。不得承诺获奖概率、保研加分、奖金、Offer、实习绿色通道或校内认定等级。

## 2. 数据源与模块边界

新增平级包：

```text
src/
  dext_recommend/     # 导师推荐
  dext_competition/   # 竞赛推荐、规则问答、备赛计划、计划助手
```

v1 只读消费本地竞赛知识库：

```text
data/竞赛助手/
  README.md
  竞赛信息总览.md
  *类竞赛规则.md
  数学建模竞赛专题.md
  高校主流竞赛规则补充文档.md
  备赛流程.md
  备赛方法指南.md
  常见问题.md
  答辩问题库.md
  网站及工具.md
  *.docx
```

数据口径：

- Markdown 是 v1 主要事实来源，必须保留文件名、章节路径、核验日期和引用片段。
- `.docx` 文件先作为待规范化资料，不直接进入线上回答；上线前应转成 Markdown 或抽取为带来源的片段，并经过人工复核。
- 具体报名日期、赛道、资格和费用具有时效性。除知识库明确写明年份和来源的事实外，响应必须提示用户回到当届官方通知和本校文件复核。
- `dext_competition` 不 import `dext_recommend`、`dext_graph` 或爬虫内部 API；如后续需要向量索引，应建立独立的 competition document index。

## 3. 后端能力契约

核心接口语义：

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

`RecommendedCompetition` 最小语义：

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

辅助能力：

```text
get_competition_detail(competition_id) -> CompetitionDetail
answer_competition_question(question, competition_id?, context?) -> GroundedAnswer
generate_preparation_plan(competition_id, student_context, plan_constraints) -> PreparationPlanDraft
diagnose_preparation_level(competition_id, student_context) -> LevelDiagnosis
suggest_plan_changes(plan, user_message) -> list[PlanChangeCard]
compare_competitions(competition_ids[2..4], student_context?) -> CompetitionComparison
```

## 4. 知识库处理与索引

v1 可以从 Markdown 直接构建轻量只读索引：

1. 扫描 `data/竞赛助手/*.md`，解析标题层级、表格、链接和 block 文本。
2. 为每个 chunk 记录 `doc_path`、`heading_path`、`last_verified`、`source_links`、`chunk_hash`。
3. 从 `竞赛信息总览.md` 抽取赛事目录，生成稳定 `competition_id`，例如正式名称规范化后的 UUID 或 slug。
4. 从方向规则文档补充 category、能力标签、典型形式、资格、组队、材料、AI/合规、准备重点和风险提示。
5. 从 `备赛流程.md`、`备赛方法指南.md`、`答辩问题库.md`、`网站及工具.md` 抽取方法论和计划模板片段。

索引可以先用结构化表 + BM25/关键词匹配；若后续引入向量检索，必须是独立 competition collection，不能复用教师 Qdrant collection 或导师 ranking profile。

知识库版本：

```text
knowledge_base_version
source_root
file_count
markdown_file_count
docx_file_count
content_hash
generated_at
```

## 5. 推荐与排序

默认 score components：

| 组件 | 初始权重 | 说明 |
|---|---:|---|
| interest_match_score | 0.30 | query、专业、兴趣与赛事方向匹配 |
| eligibility_score | 0.20 | 年级、学历、专业、组队限制是否可能满足 |
| effort_fit_score | 0.18 | 每周投入、经验等级与备赛难度匹配 |
| timeline_fit_score | 0.12 | 目标时间与常见窗口/当届时间是否冲突 |
| goal_fit_score | 0.10 | 能力提升、作品集、科研、就业技能等目标匹配 |
| provenance_score | 0.10 | 规则来源清晰度、核验日期、官方链接可用性 |

排序约束：

- 权重、阈值和 tie-break 必须进入 `competition_ranking_profile_version`。
- 目录内外赛事都可推荐，但必须标明是否在 2024 竞赛分析报告目录内，以及“学校认定需另查本校文件”。
- 不使用“含金量榜单”“获奖概率”“保研加分概率”作为排序事实。
- 当用户目标是校内认定、经费或综测加分时，系统应把“查本校文件”作为必要行动项，而不是替学校下结论。

## 6. 备赛计划与 AI 助手

计划生成输入：

```text
competition_id
target_date
weekly_hours
experience_level
time_model: "submission_deadline|competition_window"
student_context
constraints: exams, unavailable_dates, team_status, school_deadline?
```

计划输出：

```text
plan_id
competition_id
time_model
phases
tasks
optional_tasks
milestones
calendar_items
risk_register
source_refs
warnings
```

生成规则：

- 通用流程来自 `备赛流程.md`，方向方法来自对应规则文档和 `备赛方法指南.md`。
- 提交型计划保留独立 `defense_prep` 或提交后答辩阶段；目标日期变更时不能误删答辩准备。
- 窗口型计划围绕比赛窗口安排冲刺、模拟和恢复；若只有“往年常见窗口”，必须标注为不确定。
- 初学者计划必须补基础；高级用户可增加模拟赛、论文/代码复现、答辩和验收任务。
- LLM 个性化只能调整已知 phase/task schema，不能生成不受校验的自由日历。

AI 助手只输出改动卡：

```text
PlanChangeCard
  action: "move|add|delete|reschedule|appendAdvice"
  target_task_id: optional
  proposed_fields
  rationale
  source_refs
  validation_status: "pending|passed|rejected"      # 机器校验
  approval_status: "pending|accepted|declined"      # 用户审批
  application_status: "not_applied|applied|failed"   # 实际落地
```

三状态轴独立：validator 先跑（`passed` 才进审批），用户对 `passed` 的卡 `accept`/`decline`，仅 `passed`+`accepted` 的卡由 applier 原子应用。详见 [plan-assistant 子 spec](2026-06-30-dext-competition-06-plan-assistant-design.md)。

所有改动必须先经过 validator，用户 accept 后才应用。越界日期、删除必做任务、违反时间模型、缺少依据或与考试/不可用时间冲突的卡片必须拒绝或降级为建议。

## 7. 解释、引用与安全边界

输出必须区分三类内容：

- `fact`：来自知识库或官方链接的赛事事实。
- `advice`：基于备赛流程和用户上下文的建议。
- `uncertain`：需要当届通知或本校文件复核的信息。

禁止：

- 承诺获奖概率、保研/奖学金/综测收益或企业 Offer。
- 把 2024 目录写成“教育部白名单”。
- 把往届时间、奖项比例、赛道、费用、AI 规则当成当届确定事实。
- 生成代做、挂名、伪造数据、赛中泄题、绕过查重或规避 AI 披露的建议。
- 上传或记录用户未脱敏个人数据、赛题保密材料、企业合同、密钥或未公开专利。

每条推荐和规则回答至少返回 1 个 `source_ref`：

```text
source_ref
  doc_path
  heading_path
  chunk_hash
  quote_or_summary
  official_url: optional
  last_verified: optional
```

## 8. 质量评测与门禁

评测集：

- 30-50 条竞赛推荐 query，覆盖计算机、电子信息、数学建模、机器人、工学、经管、综合创业、语言艺术和医学生命科学。
- 20 条规则问答，覆盖资格、组队、AI 使用、时间、提交物、校内认定和常见混淆。
- 10 条备赛计划样本，覆盖提交型、窗口型、初学者、高投入、低投入、考试冲突和已有团队。
- 10 条计划助手改动样本，覆盖 move/add/delete/reschedule/appendAdvice 和非法改动拒绝。

核心指标：

| 指标 | 用途 |
|---|---|
| Precision@5 | 推荐首屏相关性 |
| groundedness precision | 事实是否被 source refs 支撑 |
| rule freshness warning rate | 时效信息是否提示复核 |
| unsafe advice rejection rate | 是否拒绝违规参赛建议 |
| plan feasibility pass rate | 计划是否符合时间模型和每周预算 |
| change-card validation pass rate | 助手改动是否先校验再应用 |
| no-probability-claim rate | 是否避免获奖/保研/Offer 概率承诺 |

上线门禁：

- Markdown 知识库索引可复现生成，content hash 稳定。
- `.docx` 资料不直接进入线上回答，除非已规范化并带 source refs。
- 所有推荐、详情、规则问答和计划建议能回溯到知识库片段或明确标注需要外部复核。
- 竞赛模块不依赖导师 ACTIVE build、教师 Qdrant collection 或导师事实图。

## 9. 落地步骤与子 spec 拆分

竞赛模块按依赖序拆为 7 个子 spec，每个子 spec 自带 spec → plan → TDD 执行周期。阶段编号表示实施依赖。HTTP 契约（阶段 7）是最后实现的一步，且必须等待 App 侧补齐 `docs/api-contract.md` 与 `docs/openapi.yaml` 后才能定字段。竞赛模块不依赖上游 ACTIVE build，知识库已就绪，可端到端推进。

| 阶段 | 子 spec | 入口条件 | 退出门禁 |
|---:|---|---|---|
| 1 | [Knowledge index](2026-06-30-dext-competition-01-knowledge-index-design.md) | `data/竞赛助手/*.md` 可读 | Markdown 扫描、chunk、source refs、content hash、轻量 structured index 可复现生成 |
| 2 | [Competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md) | 阶段 1 索引就绪 | 稳定 `competition_id`、category/tag、资格/赛制/组队/材料/AI/合规字段抽取完成 |
| 3 | [Recommend core](2026-06-30-dext-competition-03-recommend-core-design.md) | 阶段 2 目录可用 | `CompetitionRecommendRequest -> CompetitionRecommendResponse` 全链路可用，含理解、召回、排序、解释、风险 |
| 4 | [Detail and grounded QA](2026-06-30-dext-competition-04-detail-qa-design.md) | 阶段 2 目录 + 阶段 1 索引可用 | 竞赛详情与规则问答可用，强制 source refs 与时效/复核 warning |
| 5 | [Plan generator](2026-06-30-dext-competition-05-plan-generator-design.md) | 阶段 2 模板 + 备赛流程文档可用 | 提交型/窗口型时间模型计划生成可用，含模板兜底与必做任务保留 |
| 6 | [Plan assistant](2026-06-30-dext-competition-06-plan-assistant-design.md) | 阶段 5 计划 + 共享 grounded-generation 可用 | 改动卡生成、校验、accept/decline 应用边界可用 |
| 7 | [HTTP adapter and contract tests](2026-06-30-dext-competition-07-http-contract-design.md) | App 侧补齐 OpenAPI 契约 + 阶段 3/4/5/6 可用 | 竞赛推荐、详情、备赛计划、AI 助手接口对齐与端到端契约测试通过 |

### 9.1 上游前置

竞赛模块 v1 只读消费 `data/竞赛助手/` 本地知识库，不依赖导师 ACTIVE build、教师 Qdrant collection 或导师事实图，也不 import `dext_recommend`、`dext_graph` 或爬虫内部 API。`.docx` 文件先作为待规范化资料，不直接进入线上回答；上线前应转成 Markdown 或抽取为带来源的片段，并经过人工复核。具体报名日期、赛道、资格和费用具有时效性，除知识库明确写明年份和来源的事实外，响应必须提示用户回到当届官方通知和本校文件复核。
