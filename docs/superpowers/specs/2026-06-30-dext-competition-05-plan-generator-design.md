# 阶段 5：dext_competition plan generator

> 状态：设计稿
>
> 前置依赖：[阶段 2 competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md)模板 + `备赛流程.md`/`备赛方法指南.md`可用
>
> 后续阶段：[Plan assistant](2026-06-30-dext-competition-06-plan-assistant-design.md)

## 1. 目标

实现备赛计划生成器：基于通用流程与方向模板生成计划，支持提交型/窗口型时间模型。LLM 个性化只能调整已知 phase/task schema，不能生成不受校验的自由日历。本阶段不实现 AI 助手改动卡（阶段 6）。

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
  source_refs
  warnings
```

`source_refs` 来自 `备赛流程.md`、`备赛方法指南.md` 与对应方向规则文档；计划中的方法论步骤必须可回溯。

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
- 仍附 `source_refs`。

## 6. 时间模型不变量

- 提交型：目标日期变更重排时仅重排前置阶段，`defense_prep` 原样保留。
- 窗口型：围绕比赛窗口安排；只有往年常见窗口时标注不确定。
- 越界日期、删除必做任务、违反时间模型、与考试/不可用时间冲突的计划必须拒绝或降级为建议。

## 7. 与共享契约对齐

AI 个性化走共享 `LLMGenerationPort` + `CitationValidator`：LLM 输出只能是已知 phaseKey 下的可选任务调整，纯 JSON，客户端解析校验。超 schema 输出由 `CitationValidator`/校验器剔除。

## 8. 验收标准

- 提交型/窗口型时间模型计划均可生成。
- 提交型目标日期重排不误删 `defense_prep`；窗口型只有往年窗口时标注不确定。
- 初学者计划补基础；高级用户含模拟赛/答辩/验收。
- AI 个性化只调整已知 phase/task schema；超 schema 输出被剔除。
- AI 失败兜底返回模板计划且必做任务保留，附 `generation_fallback` warning。
- 越界/删必做/违反时间模型/与考试冲突的计划被拒绝或降级。
- 评测样本覆盖 10 条计划：提交型/窗口型/初学者/高投入/低投入/考试冲突/已有团队。
- `plan feasibility pass rate` 达标。
