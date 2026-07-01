# 阶段 6：dext_competition plan assistant

> 状态：设计稿
>
> 前置依赖：[阶段 5 plan generator](2026-06-30-dext-competition-05-plan-generator-design.md)计划可用 + [共享 grounded-generation](2026-06-30-dext-grounded-generation-design.md)
>
> 后续阶段：[HTTP adapter](2026-06-30-dext-competition-07-http-contract-design.md)

## 1. 目标

实现备赛 AI 助手：用户自然语言请求 → 生成改动卡（move_task/add_task/delete_task/reschedule_phase/append_advice）→ 校验 → 返回可审批的 change set。AI 不直接改计划；用户 accept/decline 与实际落地由应用层基于计划快照和 card status 执行。本阶段不实现 HTTP（阶段 7）。

## 2. 接口

按 overview §3/§6：

```text
suggest_plan_changes(plan_snapshot, user_message, history?, request_id) -> PreparationAssistantResult
```

助手历史独立持久化（每计划最近若干轮），由应用层管理；推荐/竞赛核心不持有助手会话状态。

## 3. PlanChangeCard

按 overview §6：

```text
PlanChangeCard
  type: "move_task|add_task|delete_task|reschedule_phase|append_advice"
  target_task_id: optional
  proposed_fields
  rationale
  status: "pending|rejected|applied|declined|stale"  # 公开/OpenAPI 状态
  rejection_code: optional
  internal_source_refs: list[SourceRef]
  internal_validation_status: "pending|passed|rejected"
  internal_approval_status: "pending|accepted|declined"
  internal_application_status: "not_applied|applied|failed"
```

`rationale` 必须基于备赛流程/方法指南片段或用户上下文；后端内部 `internal_source_refs` 可回溯。无来源的改动理由由 `CitationValidator` 降级。公开 API 默认不暴露 `internal_source_refs`。

三状态轴的生命周期：

1. 生成时内部三轴初始化：`internal_validation_status=pending`、`internal_approval_status=pending`、`internal_application_status=not_applied`，公开 `status=pending`。
2. validator 先跑：通过后保持 `status=pending` 供用户审批；拒绝时公开 `status=rejected` 并填 `rejection_code`。
3. 用户 accept/decline 由应用层处理：decline 后公开 `status=declined`，不落地。
4. 应用层只对 validator 通过且用户 accepted 的卡在当前 `base_plan_revision` 上落地；应用成功公开 `status=applied`，计划版本变化或卡片过期公开 `status=stale`。

这样保留核心内部的三状态轴，同时对齐 `docs/appside/openapi.yaml` 暴露的单一 `PreparationChangeCardStatus`。

## 4. 校验与落地边界

所有改动必须先经过 validator，用户 accept 后才应用：

- 越界日期：`status=rejected`，`rejection_code=date_out_of_range`。
- 删除必做任务：`status=rejected`，`rejection_code=required_task_delete_forbidden`。
- 违反时间模型（提交型误删 `defense_prep`、窗口型超出窗口）：`status=rejected`，`rejection_code=phase_schedule_invalid`。
- 缺少依据（无内部 `SourceRef` 且非 `advice`）：`status=rejected` 或降级为 append advice。
- 与考试/不可用时间冲突：`status=rejected` 或降级为 append advice。

`status=rejected` 的卡不进入可落地审批，不落地。实际 accept/decline 和 plan mutation 属于应用层；竞赛核心只输出可验证的 change set 与 rejection reason，不直接写用户计划。

## 5. 受约束生成

`suggest_plan_changes` 是 async 入口，通过 `await LLMGenerationPort.generate(...)` 走共享端口：

- 输入：当前 plan（阶段 5 `PreparationPlanDraft`）+ 备赛流程/方法片段（映射为共享 `FactBundle`）+ 用户消息。
- 输出：纯 JSON 的 `PreparationAssistantResult`，其中 `PlanChangeCard.type` 只允许五种 OpenAPI 枚举。
- `CitationValidator` 校验 `rationale` 的内部 `SourceRef` 可回溯。
- `SafetyGuard` 拦截违规建议（代做/挂名/伪造数据/泄题/绕查重/规避 AI 披露）。

超 schema 输出（未知 action、非法字段）被剔除，不交付半结构化结果。

## 6. 安全边界

- 不承诺获奖/保研/Offer 概率。
- 不生成代做、挂名、伪造数据、赛中泄题、绕过查重或规避 AI 披露的改动建议。
- 不上传或记录用户未脱敏个人数据、赛题保密材料、企业合同、密钥或未公开专利。

## 7. 失败模式

- LLM provider 不可用：返回 `generation_unavailable`，不静默直接改计划。
- 输出无法解析为 `PreparationAssistantResult` / `PlanChangeCard` 列表：返回 `generation_parse_error`，不交付半结构化结果。
- 全部卡被 validator 拒绝：返回空列表 + `no_valid_changes` warning，不强行落地。

## 8. 验收标准

- 助手只输出改动卡，AI 不直接改计划；accept/decline 与实际落地由应用层处理。
- 五种 OpenAPI card type 覆盖；越界/删必做/违反时间模型/缺依据/与考试冲突的卡被拒绝或降级。
- `rationale` 后端内部附 `SourceRef`；无来源降级。
- 违规建议由共享 `SafetyGuard` 拦截。
- 评测样本覆盖 10 条助手样本：move_task/add_task/delete_task/reschedule_phase/append_advice 与非法改动拒绝。
- `change-card validation pass rate` 与 `no-probability-claim rate` 达标。
