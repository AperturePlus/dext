# 阶段 5：dext_competition plan generator and level diagnosis

> 状态：设计稿
>
> 前置依赖：[阶段 2 competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md)模板 + `备赛流程.md`/`备赛方法指南.md`可用
>
> 后续阶段：[Plan assistant](2026-06-30-dext-competition-06-plan-assistant-design.md)

## 1. 目标

实现备赛计划生成器与水平诊断：基于通用流程与方向模板生成计划，支持提交型/窗口型时间模型；根据 App 问卷和用户档案给出经验等级建议。LLM 个性化只能调整已知 phase/task schema，不能生成不受校验的自由日历。本阶段不实现 AI 助手改动卡（阶段 6）。

## 2. 输入

按 overview §6：

```text
generate_preparation_plan(
  competition_id,
  target_date,
  weekly_hours,
  experience_level,
  time_model: "submission_deadline|competition_window",
  student_context,
  constraints: exams, unavailable_dates, team_status, school_deadline?
) -> PreparationPlanDraft
```

## 3. 输出

```text
PreparationPlanDraft
  plan_id
  competition_id
  time_model
  phases
  tasks
  optional_tasks
  milestones
  calendar_items
  risk_register
  internal_source_refs
  warnings
```

`internal_source_refs` 来自 `备赛流程.md`、`备赛方法指南.md` 与对应方向规则文档；计划中的方法论步骤必须在后端内部可回溯。公开 API 默认只返回计划摘要、个性化建议和 warning，不返回 Markdown 路径或 chunk hash。

## 4. 生成管线

```text
universal flow (备赛流程.md)
  -> direction template (对应规则文档 + 备赛方法指南.md)
  -> experience補基礎 (beginner 加额外必做任务)
  -> budget select optional tasks
  -> AI personalization (受约束 LLM，只调整已知 phase/task schema)
  -> schedule (排期)
  -> assemble
```

生成规则按 overview §6：

- 通用流程来自 `备赛流程.md`，方向方法来自对应规则文档和 `备赛方法指南.md`。
- 提交型计划保留独立 `defense_prep` 或提交后答辩阶段；目标日期变更时不能误删答辩准备。
- 窗口型计划围绕比赛窗口安排冲刺、模拟和恢复；若只有“往年常见窗口”，必须标注为不确定。
- 初学者计划必须补基础；高级用户可增加模拟赛、论文/代码复现、答辩和验收任务。
- LLM 个性化只能调整已知 phase/task schema，不能生成不受校验的自由日历。

## 5. 模板兜底契约

AI 失败兜底返回标准模板计划，**必做任务始终保留**。这与共享 `generation_unavailable` 的“不静默回退”原则不冲突——计划生成是唯一允许模板兜底的场景，因为通用流程与方向模板本身就是可追溯的事实来源。兜底计划必须：

- 保留全部必做任务。
- 附 `generation_fallback` warning 说明走了模板路径。
- 后端内部仍附 `SourceRef`。

## 6. 水平诊断

按 `docs/appside/openapi.yaml` 的 `/preparation-plans/diagnose` 契约提供：

```text
diagnose_preparation_level(
  competition: CompetitionSnapshot,
  answers: list[PreparationDiagnoseAnswer],
  profile: UserProfile | null
) -> LevelDiagnosis

LevelDiagnosis
  level: "beginner|intermediate|experienced"
  rationale
  suggestion
```

诊断只基于问卷答案、用户授权档案和竞赛快照生成经验等级建议；不得把诊断写入竞赛知识库，不得承诺获奖概率、校内认定或升学收益。`rationale` 可引用用户问卷摘要和内部事实片段，但公开 API 默认不暴露 `SourceRef`。

## 7. 时间模型不变量

- 提交型：目标日期（提交截止）变更重排时，保留 `defense_prep` 阶段及其任务**结构**，但按新提交日期重新锚定其排期——`defense_prep` 起点须落在新提交日之后，整体阶段链相对新提交日顺延，不得冻结在旧日期（否则答辩会停在旧日期甚至落到提交日前）。仅前置阶段随提交日重排，`defense_prep` 的内部任务不变、起止日期随锚点平移。
- 窗口型：围绕比赛窗口安排；只有往年常见窗口时标注不确定。
- 越界日期、删除必做任务、违反时间模型、与考试/不可用时间冲突的计划必须拒绝或降级为建议。

## 8. 与共享契约对齐

AI 个性化走共享 `LLMGenerationPort` + `CitationValidator`：LLM 输出只能是已知 phaseKey 下的可选任务调整，纯 JSON，客户端解析校验。超 schema 输出由 `CitationValidator`/校验器剔除。

## 9. 验收标准

- 提交型/窗口型时间模型计划均可生成。
- `/preparation-plans/diagnose` 对应的水平诊断可生成 `level/rationale/suggestion`，且不输出概率承诺。
- 提交型目标日期重排不误删 `defense_prep`；窗口型只有往年窗口时标注不确定。
- 初学者计划补基础；高级用户含模拟赛/答辩/验收。
- AI 个性化只调整已知 phase/task schema；超 schema 输出被剔除。
- AI 失败兜底返回模板计划且必做任务保留，附 `generation_fallback` warning。
- 越界/删必做/违反时间模型/与考试冲突的计划被拒绝或降级。
- 评测样本覆盖 10 条计划：提交型/窗口型/初学者/高投入/低投入/考试冲突/已有团队。
- `plan feasibility pass rate` 达标。
