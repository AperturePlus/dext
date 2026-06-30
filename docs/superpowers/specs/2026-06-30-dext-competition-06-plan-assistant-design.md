# 阶段 6：dext_competition plan assistant

> 状态：设计稿
>
> 前置依赖：[阶段 5 plan generator](2026-06-30-dext-competition-05-plan-generator-design.md)计划可用 + [共享 grounded-generation](2026-06-30-dext-grounded-generation-design.md)
>
> 后续阶段：[HTTP adapter](2026-06-30-dext-competition-07-http-contract-design.md)

## 1. 目标

实现备赛 AI 助手：用户自然语言请求 → 生成改动卡（move/add/delete/reschedule/appendAdvice）→ 校验 → 用户逐张 accept/decline → accept 的卡才落地。把“AI 改日历”做成“提议—审批—落地”的人机协作闭环，AI 不直接改计划。本阶段不实现 HTTP（阶段 7）。

## 2. 接口

按 overview §3/§6：

```text
suggest_plan_changes(plan, user_message) -> list[PlanChangeCard]
```

助手历史独立持久化（每计划最近若干轮），由应用层管理；推荐/竞赛核心不持有助手会话状态。

## 3. PlanChangeCard

按 overview §6：

```text
PlanChangeCard
  action: "move|add|delete|reschedule|appendAdvice"
  target_task_id: optional
  proposed_fields
  rationale
  source_refs: list[SourceRef]
  # 三个独立状态轴，不混用单一 enum
  validation_status: "pending|passed|rejected"      # 机器校验：越界/删必做/违反时间模型/缺依据/冲突
  approval_status: "pending|accepted|declined"       # 用户审批：用户主动 accept/decline
  application_status: "not_applied|applied|failed"   # 实际落地：accept 后由 applier 应用
```

`rationale` 必须基于备赛流程/方法指南片段或用户上下文；`source_refs` 可回溯。无来源的改动理由由 `CitationValidator` 降级。

三状态轴的生命周期：

1. 生成时三轴初始化：`validation_status=pending`、`approval_status=pending`、`application_status=not_applied`。
2. validator 先跑：`passed` 才进入用户审批；`rejected` 直接终止，不向用户展示可落地选项。
3. 用户对 `validation_status=passed` 的卡 `accept`/`decline`：`declined` 终止，不落地。
4. 仅 `validation_status=passed` 且 `approval_status=accepted` 的卡由 `plan_change_applier` 原子应用，应用成功置 `application_status=applied`，失败置 `failed` 并回滚。

这样能区分“校验通过待审批”“用户主动拒绝”“已应用/应用失败”，不再用单一 enum 表达三个正交维度。

## 4. 校验与落地边界

所有改动必须先经过 validator，用户 accept 后才应用：

- 越界日期：`validation_status=rejected`。
- 删除必做任务：`validation_status=rejected`。
- 违反时间模型（提交型误删 `defense_prep`、窗口型超出窗口）：`validation_status=rejected`。
- 缺少依据（无 `source_refs` 且非 `advice`）：`validation_status=rejected` 或降级为建议。
- 与考试/不可用时间冲突：`validation_status=rejected` 或降级为建议。

`validation_status=rejected` 的卡不进入用户审批，不落地。仅 `validation_status=passed` 且 `approval_status=accepted` 的卡由 `plan_change_applier` 原子应用，应用失败置 `application_status=failed` 并回滚。

## 5. 受约束生成

`suggest_plan_changes` 走共享 `LLMGenerationPort`：

- 输入：当前 plan（阶段 5 `PreparationPlanDraft`）+ 备赛流程/方法片段（映射为共享 `FactBundle`）+ 用户消息。
- 输出：纯 JSON 的 `PlanChangeCard` 列表，只允许五种 action 枚举。
- `CitationValidator` 校验 `rationale` 的 `source_refs` 可回溯。
- `SafetyGuard` 拦截违规建议（代做/挂名/伪造数据/泄题/绕查重/规避 AI 披露）。

超 schema 输出（未知 action、非法字段）被剔除，不交付半结构化结果。

## 6. 安全边界

- 不承诺获奖/保研/Offer 概率。
- 不生成代做、挂名、伪造数据、赛中泄题、绕过查重或规避 AI 披露的改动建议。
- 不上传或记录用户未脱敏个人数据、赛题保密材料、企业合同、密钥或未公开专利。

## 7. 失败模式

- LLM provider 不可用：返回 `generation_unavailable`，不静默直接改计划。
- 输出无法解析为 `PlanChangeCard` 列表：返回 `generation_parse_error`，不交付半结构化结果。
- 全部卡被 validator 拒绝：返回空列表 + `no_valid_changes` warning，不强行落地。

## 8. 验收标准

- 助手只输出改动卡，accept 后才落地；AI 不直接改计划。
- 五种 action 覆盖；越界/删必做/违反时间模型/缺依据/与考试冲突的卡被拒绝或降级。
- `rationale` 附 `source_refs`；无来源降级。
- 违规建议由共享 `SafetyGuard` 拦截。
- 评测样本覆盖 10 条助手样本：move/add/delete/reschedule/appendAdvice 与非法改动拒绝。
- `change-card validation pass rate` 与 `no-probability-claim rate` 达标。
