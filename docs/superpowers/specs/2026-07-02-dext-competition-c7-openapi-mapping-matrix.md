# C7-prep: OpenAPI owned-path mapping matrix

> 状态：只读分析稿（C7-prep）。本文不写任何 handler、repository 代码或 SQL migration；
> 仅为 C7 实施阶段建立 owned-path 字段映射、契约缺口、HTTP status/error 映射、fixture
> 清单与 PostgreSQL repository 契约草图。
>
> 来源契约（frozen source of truth）：
> - `docs/appside/openapi.yaml`（App 侧 HTTP 契约，**不得修改**）
> - G0 共享契约（commit `3bb4c10`，可从 `dext_competition` import）：
>   `contracts/{recommend,qa,plan,assistant,catalog,knowledge}.py`、`errors.py`
> - 概览 §6（三状态轴 → 单一 `status` 折叠规则）：
>   `docs/superpowers/specs/2026-06-30-dext-competition-assistant-design.md`
> - C7 spec：`docs/superpowers/specs/2026-06-30-dext-competition-07-http-contract-design.md`
> - 并行方案 §7：`docs/superpowers/specs/2026-07-02-dext-competition-parallel-implementation-plan.md`
>
> OpenAPI base path：`servers.url=/api/v1`；下表路径省略该前缀（与 C7 spec §3 一致）。
> 所有响应均为 `allOf: [EnvelopeBase, { data: <T> }]` 结构，`EnvelopeBase` 含
> `code: int`、`message: str`、`data`。

---

## 1. Owned-path mapping matrix

competition 模块“拥有”的路径 = 其语义映射到 recommend / detail / QA / plan /
assistant / catalog G0 契约的路径。**共享 application-state 路径**（identity /
profile / favorites / history / chat / `/account/remote-data`）由共享 App/API 层
拥有（R7b / C7 spec §5），competition 仅作为 `/account/remote-data` 的 cleanup
participant 与 preparation-plans / assistant-history repository 的实现方参与，故
单独列入“共享/参与”表，不计入 competition 独占 owned-path。

### 1.A competition 独占 owned paths

| OpenAPI path+method | Request schema | Response schema | Maps to G0 contract | Status/error codes | Owner | Notes |
|---|---|---|---|---|---|---|
| `GET /competitions` | — (无 body) | `CompetitionCatalogEnvelope` (data: `array[RecommendedCompetition]`) | `CompetitionCard`（catalog reader，C2）→ adapter 投影为公开 `RecommendedCompetition` 字段子集 | 200；见 §3 | C3/C7 | OpenAPI 复用 `RecommendedCompetition` 同一 schema 作为目录项；G0 `CompetitionCard` 与 `RecommendedCompetition` 字段差异见 §1.B 字段映射 + §2 缺口 G1 |
| `GET /competitions/{competition_id}` | path `competition_id: string` | `RecommendedCompetitionEnvelope` (data: `RecommendedCompetition`) | `CompetitionDetail`（C4 `get_competition_detail`）→ 公开投影 | 200；404（catalog 无此 id）；见 §3 | C4/C7 | OpenAPI 用 `RecommendedCompetition` 作为详情载体，但 G0 详情契约是 `CompetitionDetail`（含 `eligibility/schedule/team_policy/materials/ai_compliance/preparation_focus`）。字段不匹配见 §2 缺口 G2 |
| `POST /recommendations/competitions` | `CompetitionRecommendationRequest` {prompt, session_id?, profile?} | `CompetitionRecommendationEnvelope` (data: `CompetitionRecommendationResult`) | `CompetitionRecommendRequest`/`CompetitionRecommendResponse`/`RecommendedCompetition`/`CompetitionWarning` | 200；400/422（needs_clarification / invalid_request）；见 §3 | C3/C7 | 请求/响应字段重映射见 §1.B；`profile` 是 `UserProfile`（OpenAPI 全局 schema）而非 G0 `StudentContext`/`CompetitionPreferences`，见 §2 缺口 G3/G4 |
| `POST /preparation-plans/generate` | `PreparationPlanGenerateRequest` | `PreparationPlanGenerateEnvelope` (data: `PreparationPlanGenerateResult`) | `PreparationPlanDraft`（C5 `generate_preparation_plan`）+ `PlanTask` | 200；422（plan_invalid）；见 §3 | C5/C7 | OpenAPI 请求是“个性化增量”语义（competition snapshot + phase_keys + 可选 user_profile），G0 `PreparationPlanDraft` 是完整草案（phases/tasks/optional_tasks/milestones/...）。字段差异见 §2 缺口 G5 |
| `GET /preparation-plans` | — | `PreparationPlanListEnvelope` (data: `array[PreparationAssistantPlanSnapshot]`) | （应用层 repository）`PreparationPlanDraft` 的持久化快照 | 200；401（未鉴权）；见 §3 | C7（repo） | 列表仅返回当前 owner_id 的快照；competition core 不写用户数据，repository 见 §5 |
| `POST /preparation-plans` | `PreparationAssistantPlanSnapshot` + header `Idempotency-Key` | `PreparationPlanEnvelope` (data: `PreparationAssistantPlanSnapshot`) | repository：create/replace 当前 owner 的 plan snapshot（按 client `id`+`revision`） | 200；401；409（revision 冲突）；见 §3 | C7（repo） | 描述为“create or replace current identity's plan snapshot using the client-generated plan id and revision” |
| `GET /preparation-plans/{plan_id}` | path `plan_id: string` | `PreparationPlanEnvelope` | repository：load one | 200；401；404；见 §3 | C7（repo） | `plan_id` 必须等于 `plan_snapshot.id`（OpenAPI param 描述） |
| `PUT /preparation-plans/{plan_id}` | `PreparationAssistantPlanSnapshot` + header `Idempotency-Key` | `PreparationPlanEnvelope` | repository：save new revision | 200；401；404；409（plan_revision_stale）；见 §3 | C7（repo） | 应用层拥有 revision check 与 persistence；competition core 仅生成内容与 change cards（OpenAPI desc 明示） |
| `DELETE /preparation-plans/{plan_id}` | path `plan_id: string` | `DeletedEnvelope` | repository：delete plan + assistant history | 200；401；404；见 §3 | C7（repo） | “delete one persisted preparation plan and its assistant history” |
| `GET /preparation/config` | — | `PreparationConfigEnvelope` (data: `PreparationConfig`) | （应用层 + competition config） | 200；401；见 §3 | C7 | server-managed 配置；`category_aliases/timeline_defaults/prior_experience_options/domain_familiarity_options`。无对应 G0 契约，见 §2 缺口 G6 |
| `GET /preparation-templates` | query: `timeline_type` (enum eventWindow|submission), `include_defense: bool`, `category: string`, `competition_id: string` | `PreparationTemplateEnvelope` (data: `PreparationTemplate`) | C5 plan template reader | 200；401；404（category/competition 未命中）；见 §3 | C5/C7 | `CompetitionTimelineType` enum `{eventWindow, submission}` ≠ G0 `PreparationPlanDraft.time_model` 字面量 `{submission_deadline|competition_window}`，见 §2 缺口 G7 |
| `POST /preparation-plans/diagnose` | `PreparationDiagnoseRequest` {competition, profile?, answers} | `PreparationDiagnoseEnvelope` (data: `PreparationDiagnoseResult` {level, rationale, suggestion}) | `LevelDiagnosis`（C5 `diagnose_preparation_level`） | 200；422（invalid_request）；见 §3 | C5/C7 | 字段语义对齐良好：`level/rationale/suggestion`。OpenAPI `level` enum `{beginner, intermediate, experienced}` = G0 `LevelDiagnosis.level` 注释 |
| `POST /preparation-plans/{plan_id}/assistant` | `PreparationAssistantRequest` + path `plan_id` | `PreparationAssistantEnvelope` (data: `PreparationAssistantResult`) | `PlanChangeCard`（C6 `suggest_plan_changes`） | 200；401；404；409（plan_revision_stale）；422（change_card_rejected）；见 §3 | C6/C7 | 三状态轴 → 单一 `status` 折叠规则见 §1.C；`change_set.cards` maxItems 5 |

### 1.B 字段名映射（OpenAPI ↔ G0，差异处）

#### `RecommendedCompetition`（OpenAPI schema）↔ G0 `RecommendedCompetition`（contracts/recommend.py）

| OpenAPI field | G0 field | 映射方向 | 说明 |
|---|---|---|---|
| `id` | `competition_id` | rename | OpenAPI 用 `id`，G0 用 `competition_id` |
| `name` | `display_name` | rename | |
| `category` | `category` | direct | |
| `level` | （G0 无） | **缺失** | OpenAPI 有 `level`，G0 `RecommendedCompetition` 无对应字段。见 §2 缺口 G8 |
| `tags` | （G0 无） | **缺失** | OpenAPI 有 `tags`；G0 `RecommendedCompetition` 无。`CompetitionCard` 有 `tags`，但 `RecommendedCompetition` 无。见 §2 缺口 G8 |
| `team_size` | `team_notes` | 投影/降维 | OpenAPI `team_size: string`（如 "3-5人"），G0 `team_notes: str`（自由文本）。adapter 需从 notes 抽取或两者映射见 §2 缺口 G9 |
| `signup_time` | `schedule_notes` | 投影/降维 | OpenAPI 把 schedule 拆成 `signup_time`/`contest_time`；G0 是单一 `schedule_notes`。见 §2 缺口 G9 |
| `contest_time` | `schedule_notes` | 投影/降维 | 同上 |
| `format` | （G0 无） | **缺失** | OpenAPI `format`；G0 `RecommendedCompetition` 无。`CompetitionCard` 也无。见 §2 缺口 G8 |
| `organizer` | （G0 无） | **缺失** | OpenAPI `organizer`；G0 无。见 §2 缺口 G8 |
| `official_url` | `official_links: tuple[str,...]` | 投影 | OpenAPI 单个 `official_url: string`；G0 是 tuple。adapter 取首个或主链接。见 §2 缺口 G10 |
| `reason` | `short_reasons: tuple[str,...]` | 投影 | OpenAPI 单 `reason: string`；G0 是多条。adapter join 或取主因。见 §2 缺口 G10 |
| `preparation_tips` | `preparation_effort` + `eligibility_notes`? | **歧义** | OpenAPI `preparation_tips: array[str]`；G0 `preparation_effort: str`（投入估算）。语义不同。见 §2 缺口 G11 |
| `limitations` | `risk_flags: tuple[str,...]` | 投影 | OpenAPI `limitations: array[str]`；G0 `risk_flags`。语义近似但命名不同。adapter 需定义投影。见 §2 缺口 G10 |
| `match_score` | `score: float` | rename | OpenAPI `[0,1]`；G0 `score: float`（范围未约束）。见 §2 缺口 G12 |
| （OpenAPI 无） | `summary` | 隐藏/降级 | G0 有 `summary`；OpenAPI `RecommendedCompetition` 无。可能映射到 `reason` 或省略 |
| （OpenAPI 无） | `fit_level` | 隐藏 | G0 `fit_level`；OpenAPI 无。adapter 默认隐藏，diagnostics 模式可回填 |
| （OpenAPI 无） | `score_components` | 隐藏 | G0 诊断字段；OpenAPI 无。diagnostics/debug 模式回填 |
| （OpenAPI 无） | `eligibility_notes` | 隐藏/投影 | G0 有；OpenAPI `RecommendedCompetition` 无（但 `CompetitionDetail` 有 `eligibility`）。见 §2 缺口 G13 |
| （OpenAPI 无） | `evidence_status` | 隐藏/投影 | G0 `evidence_status: grounded|partial|uncertain`；OpenAPI 无显式字段。可能折进 `freshness_notice`? 见 §2 缺口 G13 |
| （OpenAPI 无） | `freshness_notice` | 隐藏/投影 | G0 有；OpenAPI `RecommendedCompetition` 无（但 `CompetitionRulesSummary.official_url` 间接相关）。见 §2 缺口 G13 |
| （OpenAPI 无） | `internal_source_refs` | 隐藏 | G0 内部字段；C7 spec §4 / 概览 §7 明确默认不返回，仅 diagnostics/debug/admin 模式 |
| （OpenAPI 无） | `short_reasons/risk_flags/official_links` | 隐藏/投影 | 见上 |

#### `CompetitionRecommendationRequest`（OpenAPI）↔ G0 `CompetitionRecommendRequest`

| OpenAPI field | G0 field | 映射方向 | 说明 |
|---|---|---|---|
| `prompt` | `query_text` | rename | |
| `session_id` | （G0 无） | **新增** | OpenAPI 有 `session_id`；G0 `CompetitionRecommendRequest` 无。会话归属由应用层管理。见 §2 缺口 G4 |
| `profile: UserProfile` | `preferences: CompetitionPreferences` + `student_context: StudentContext` | **重映射** | OpenAPI 用全局 `UserProfile`；G0 用 `CompetitionPreferences`（categories/major/grade/experience_level/weekly_hours/target_goal/team_preference/time_window/risk_tolerance）+ `StudentContext`。字段不一一对应。见 §2 缺口 G3 |
| （OpenAPI 无） | `limit: int = 6` | 缺失 | OpenAPI 请求无 `limit`；G0 有。默认值或 server 配置见 §2 缺口 G4 |
| （OpenAPI 无） | `diagnostics_level` | 缺失 | OpenAPI 无；G0 `none|summary|debug`。仅服务端开关。见 §2 缺口 G4 |

#### `CompetitionRecommendationResult`（OpenAPI）↔ G0 `CompetitionRecommendResponse`

| OpenAPI field | G0 field | 映射方向 | 说明 |
|---|---|---|---|
| `session_id` | （G0 无） | 新增 | 应用层回填 |
| `understanding: CompetitionUnderstanding` | `query_understanding: object` | rename+结构 | OpenAPI `CompetitionUnderstanding` {directions, categories, timing_preferences, team_preferences, uncertainties}；G0 `query_understanding` 是 `object`（C3 定义 `CompetitionQueryUnderstanding`）。结构需对齐见 §2 缺口 G14 |
| `recommendations: array[RecommendedCompetition]` | `results: tuple[RecommendedCompetition,...]` | rename+字段差异 | 元素字段差异见上 §1.B 第一表 |
| `follow_up_questions: array[string]` | （G0 无） | 新增 | OpenAPI 有；G0 `CompetitionRecommendResponse` 无。adapter 由 C3 输出补齐见 §2 缺口 G4 |
| （OpenAPI 无） | `knowledge_base_version` | 隐藏/日志 | G0 有；OpenAPI 响应无。仅日志记录（C7 spec §6） |
| （OpenAPI 无） | `warnings: tuple[CompetitionWarning,...]` | **缺失/折叠** | G0 有 `CompetitionWarning`（code/message/competition_id）；OpenAPI 响应**无 warnings 字段**。stale_fact/unsafe 等如何回传？见 §2 缺口 G15（重大缺口） |

### 1.C PlanChangeCard 三状态轴 → 单一 `status` 折叠规则（概览 §6）

G0 `PlanChangeCard`（contracts/assistant.py）有三个独立状态轴：

```text
validation_status : pending | passed | rejected      # 机器 validator
approval_status   : pending | accepted | declined    # 用户审批
application_status : not_applied | applied | failed   # 实际落地
```

OpenAPI `PreparationChangeCard.status` 单一轴（`PreparationChangeCardStatus`）：

```text
status: pending | rejected | applied | declined | stale
```

折叠规则（概览 §6：validator 先跑 → passed 才进审批 → accept 后应用）：

| validation_status | approval_status | application_status | → OpenAPI `status` | 说明 |
|---|---|---|---|---|
| `pending` | `pending` | `not_applied` | `pending` | validator 未跑或未定 |
| `passed` | `pending` | `not_applied` | `pending` | 校验通过待审批 |
| `rejected` | （任意） | （任意） | `rejected` | validator 拒绝；带 `rejection_code`/`rejection_reason` |
| `passed` | `declined` | `not_applied` | `declined` | 用户拒绝 |
| `passed` | `accepted` | `applied` | `applied` | 已落地 |
| `passed` | `accepted` | `failed` | `stale` | 落地失败 → 标记 stale |
| `passed` | `accepted` | `not_applied` | `pending` | 已 accept 未落地（瞬态） |

OpenAPI `PreparationChangeCardRejectionCode` enum 与折叠后 `status=rejected` 配对：

```text
missing_required_fields | target_task_not_found | target_phase_not_found |
completed_task_protected | date_out_of_range | invalid_add_task_fields |
required_task_delete_forbidden | phase_schedule_invalid | invalid_advice_fields
```

这些 `rejection_code` 对应 G0 validator 的拒绝原因（C6 spec）；G0 `PlanChangeCard`
本身**无 `rejection_code` 字段**——见 §2 缺口 G16。adapter 需从 C6 validator 输出补齐。

`PreparationAssistantHistoryTurn.card_results[].status`（OpenAPI）同样使用单一
`status` enum `{pending, rejected, applied, declined, stale}`，折叠规则同上。

#### OpenAPI `PreparationChangeCard` ↔ G0 `PlanChangeCard` 字段

| OpenAPI field | G0 field | 映射方向 | 说明 |
|---|---|---|---|
| `id` | （G0 无） | 新增 | OpenAPI 卡片有 `id`；G0 `PlanChangeCard` 无。adapter 生成 |
| `type: PreparationChangeCardType` | `action: str` | rename+enum映射 | OpenAPI `move_task|add_task|delete_task|reschedule_phase|append_advice`；G0 `move|add|delete|reschedule|appendAdvice`。**枚举字面量不同**（OpenAPI 带后缀 `_task`/`_phase`）。见 §2 缺口 G17 |
| `target_task_id` | `target_task_id` | direct | |
| `target_phase_key` | （G0 无显式；在 `proposed_fields` 内?） | **歧义** | OpenAPI 有 `target_phase_key`；G0 `PlanChangeCard` 无独立字段（reschedule 时 phase 写进 `proposed_fields`）。见 §2 缺口 G18 |
| `new_date: date` | （G0 在 `proposed_fields` 内） | **歧义** | OpenAPI 顶层 `new_date`；G0 无顶层字段。见 §2 缺口 G18 |
| `new_task: PreparationNewTaskDraft` | （G0 在 `proposed_fields` 内） | **歧义** | 同上 |
| `phase_schedule: array[PreparationPhaseScheduleDraft]` | （G0 在 `proposed_fields` 内） | **歧义** | 同上 |
| `advice_text: string` | （G0 在 `proposed_fields` 内） | **歧义** | 同上 |
| `summary` | （G0 无） | 新增 | OpenAPI `summary`；G0 无。adapter 生成 |
| `rationale` | `rationale` | direct | |
| `status` | `validation_status`+`approval_status`+`application_status` | **折叠** | 见上表 |
| `rejection_code` | （G0 无） | 新增 | 见缺口 G16 |
| `rejection_reason` | （G0 无） | 新增 | 见缺口 G16 |
| （OpenAPI 无） | `proposed_fields: dict` | 隐藏/拆解 | G0 用通用 dict 承载 proposed 字段；OpenAPI 把它们拆成具名顶层字段。adapter 双向拆解/合并。见 §2 缺口 G18 |
| （OpenAPI 无） | `internal_source_refs` | 隐藏 | 默认不返回 |

---

## 2. Unknown-field / missing-field contract gaps

每条记为 contract issue，**不在此解决**，留 C7 前决策。`<G#>` 为缺口编号。

- **G1（目录载体 vs 推荐载体共用 `RecommendedCompetition`）**：OpenAPI `GET /competitions`
  与 `GET /competitions/{competition_id}` 复用 `RecommendedCompetition` schema 作为
  目录/详情载体。G0 中目录是 `CompetitionCard`（catalog），详情是 `CompetitionDetail`，
  推荐结果是 `RecommendedCompetition`。三者字段不同（`CompetitionCard` 有
  `tags/eligibility/schedule/team_policy/materials/ai_compliance/preparation_focus/
  risk_flags/official_links/in_2024_catalog/last_verified`；`CompetitionDetail` 有
  `eligibility/schedule/team_policy/materials/ai_compliance/preparation_focus`；
  `RecommendedCompetition` 有 `fit_level/score/score_components/eligibility_notes/
  schedule_notes/team_notes/preparation_effort/evidence_status`）。adapter 需决定
  目录/详情投影到 `RecommendedCompetition` 的哪些字段、哪些置空。**契约 issue**。
- **G2（`GET /competitions/{competition_id}` 返回 `RecommendedCompetition` 而非
  `CompetitionDetail`）**：OpenAPI 详情端点 schema 是 `RecommendedCompetitionEnvelope`，
  但 G0 详情契约是 `CompetitionDetail`（含 `eligibility/schedule/team_policy/materials/
  ai_compliance/preparation_focus`）。`RecommendedCompetition` schema **没有这些字段**。
  C4 `get_competition_detail` 的输出无处可放。**契约 issue**（详情端点字段不足）。
- **G3（`profile: UserProfile` ↔ `CompetitionPreferences` + `StudentContext`）**：
  OpenAPI `CompetitionRecommendationRequest.profile` 是全局 `UserProfile`（name/gender/
  degree_stage/school/major/research_interests/highlights/score/competitions/research）。
  G0 `CompetitionPreferences`（categories/experience_level/weekly_hours/target_goal/
  team_preference/time_window/risk_tolerance）与 `StudentContext`。两者字段不一一对应：
  `UserProfile` 无 `weekly_hours`/`target_goal`/`team_preference`/`time_window`/
  `risk_tolerance`；`CompetitionPreferences` 无 `school`/`major`/`research_interests`/
  `score`。adapter 需定义 `UserProfile → CompetitionPreferences + StudentContext` 的
  派生规则（如 `major→categories` 推断、`experience_level` 缺失需 diagnose）。**契约 issue**。
- **G4（G0 `CompetitionRecommendRequest` 字段在 OpenAPI 缺失）**：`limit`、
  `diagnostics_level` 在 OpenAPI 请求中不存在；G0 `CompetitionRecommendResponse` 的
  `warnings`、`knowledge_base_version` 在 OpenAPI 响应中不存在；OpenAPI 响应的
  `session_id`、`follow_up_questions` 在 G0 中不存在。adapter 需决定默认值与回填源。
  **契约 issue**。
- **G5（`PreparationPlanGenerateRequest` 是“个性化增量” vs G0 `PreparationPlanDraft`
  是“完整草案”）**：OpenAPI 请求含 `competition: CompetitionSnapshot`、`phase_keys:
  array[string]`、`weekly_commitment`、`experience_level`，响应 `PreparationPlanGenerateResult`
  只含 `phases: array[PreparationPhasePersonalization]`（每 phase 的 `optional_tasks`
  maxItems 3 + `personalized_advice`）+ `global_advice`。G0 `PreparationPlanDraft` 含
  `phases/tasks/optional_tasks/milestones/calendar_items/risk_register/internal_source_refs/
  warnings/revision`。OpenAPI 响应**不含** milestones/calendar_items/risk_register/
  internal_source_refs/warnings。adapter 需决定这些 G0 字段是否在 generate 端点丢弃，
  或在持久化端点（`POST /preparation-plans`）由客户端拼装。**契约 issue**。
- **G6（`PreparationConfig` 无对应 G0 契约）**：OpenAPI `PreparationConfig`（category_aliases/
  timeline_defaults/prior_experience_options/domain_familiarity_options）是 server-managed
  配置，G0 无对应契约。C7 需新建配置源（可能来自 catalog/config.py）。**契约 issue**。
- **G7（`CompetitionTimelineType` enum 与 G0 `time_model` 字面量不同）**：OpenAPI
  `CompetitionTimelineType: {eventWindow, submission}`；G0 `PreparationPlanDraft.time_model:
  submission_deadline|competition_window`。映射建议：`eventWindow→competition_window`、
  `submission→submission_deadline`，但**未在 G0/OpenAPI 任何处成文**。**契约 issue**。
- **G8（OpenAPI `RecommendedCompetition` 有 `level/tags/format/organizer`，G0
  `RecommendedCompetition` 无）**：这四个字段 G0 推荐结果不含。`level`/`tags` 在
  `CompetitionCard` 中存在（`tags` 有，`level` 无）；`format`/`organizer` 在 G0 全无。
  数据源需明确（catalog？rules_summary？）。**契约 issue**。
- **G9（schedule 字段拆分差异）**：OpenAPI `RecommendedCompetition` 拆 `signup_time`/
  `contest_time`/`team_size`/`format`/`organizer`；G0 是 `schedule_notes`/`team_notes`/
  `preparation_effort`。结构化字段 vs 自由文本。adapter 需从 G0 notes 解析或要求 C3
  输出结构化子字段。**契约 issue**。
- **G10（单值 vs 多值投影）**：OpenAPI `official_url: string`（单）↔ G0
  `official_links: tuple`（多）；OpenAPI `reason: string`（单）↔ G0 `short_reasons:
  tuple`（多）；OpenAPI `limitations: array` ↔ G0 `risk_flags: tuple`。投影方向需
  成文（取首？join？）。**契约 issue**。
- **G11（`preparation_tips` 语义不明）**：OpenAPI `preparation_tips: array[str]`；
  G0 `preparation_effort: str`（投入估算）+ `eligibility_notes`。`preparation_tips`
  数据源未定（备赛流程文档片段？）。**契约 issue**。
- **G12（`match_score` 范围）**：OpenAPI `match_score: number, minimum 0, maximum 1`；
  G0 `score: float`（无范围约束）。adapter 需 clamp/normalize。**契约 issue**。
- **G13（G0 `evidence_status`/`freshness_notice`/`eligibility_notes`/`fit_level`/
  `summary`/`score_components` 在 OpenAPI `RecommendedCompetition` 无对应字段）**：
  这些 G0 推荐字段如何回传？折叠进 `reason`/`limitations`？还是 diagnostics 模式
  额外字段？C7 spec §4 说“公开字段映射到 reason/limitations/official_url/status/
  rationale/suggestion”，但 `evidence_status`/`freshness_notice` 无明确落点。**契约 issue**。
- **G14（`CompetitionUnderstanding` ↔ `CompetitionQueryUnderstanding` 结构未对齐）**：
  OpenAPI `CompetitionUnderstanding` {directions, categories, timing_preferences,
  team_preferences, uncertainties}；G0 `query_understanding` 是 `object`（C3 定义
  `CompetitionQueryUnderstanding`，G0 未固化字段）。C3 输出结构需与 OpenAPI 对齐或
  adapter 重映射。**契约 issue**。
- **G15（G0 `CompetitionWarning` / `warnings` 在 OpenAPI 响应中无字段）**：**重大缺口**。
  G0 `CompetitionRecommendResponse.warnings: tuple[CompetitionWarning,...]`（code/
  message/competition_id），覆盖 `stale_fact`/`unsafe_advice`/`unauthorized_contact`/
  `knowledge_base_stale` 等。OpenAPI `CompetitionRecommendationResult` **无 warnings
  字段**。stale_fact 等警告如何回传客户端？（200-with-warning 无字段可放；HTTP status
  又非业务错误）。`CompetitionError.to_dict()` 也无对应 OpenAPI envelope 字段。**契约 issue**
  （需在 OpenAPI 增补 warnings 字段，或定义 warnings 折叠进 `limitations`/`reason`）。
- **G16（G0 `PlanChangeCard` 无 `rejection_code`/`rejection_reason`/`id`/`summary`/
  `type`/`target_phase_key`/`new_date`/`new_task`/`phase_schedule`/`advice_text` 顶层字段）**：
  OpenAPI `PreparationChangeCard` 有这些顶层字段；G0 只有 `action/target_task_id/
  proposed_fields/rationale/internal_source_refs/三状态轴`。adapter 需双向拆解/合并
  `proposed_fields` dict ↔ 具名顶层字段。**契约 issue**（C6 validator 需输出
  `rejection_code`，但 G0 `PlanChangeCard` 无此字段——C6 spec 是否扩展?）。
- **G17（`PreparationChangeCardType` enum 字面量与 G0 `action` 不同）**：OpenAPI
  `move_task|add_task|delete_task|reschedule_phase|append_advice`；G0
  `move|add|delete|reschedule|appendAdvice`。映射需成文。**契约 issue**。
- **G18（`proposed_fields` dict ↔ 具名字段）**：G0 `PlanChangeCard.proposed_fields:
  dict[str, object]` 是通用 dict；OpenAPI 把 proposed 字段拆成 `target_phase_key`/
  `new_date`/`new_task`/`phase_schedule`/`advice_text` 顶层。双向映射规则需成文
  （按 `type` 决定哪些顶层字段填 `proposed_fields`）。**契约 issue**。
- **G19（OpenAPI 无 `diagnostics_level` 触发 `internal_source_refs` 回传）**：G0
  `CompetitionRecommendRequest.diagnostics_level: none|summary|debug`；OpenAPI 请求
  无此字段。`internal_source_refs`/`score_components` 在 diagnostics/debug/admin 模式
  回填（C7 spec §4、概览 §7），但 OpenAPI 无触发参数。是 header？server 开关？
  **契约 issue**。
- **G20（`/home/prompts` 与 `/home/config` 的 `mode=competition`）**：OpenAPI
  `GET /home/prompts?mode=competition` 与 `GET /home/config?mode=competition`。owner
  未在 C7 spec §3 owned 端点表中列出，但 `mode=competition` 语义属 competition。
  owner 归属待定（competition 还是共享 App 层？）。**契约 issue**（owner 归属）。
- **G21（`/preparation-templates` 必填 query `include_defense: bool`）**：OpenAPI
  要求 `include_defense` 必填；G0 `PreparationPlanDraft` 的 `defense_prep` 不变量
  （C5 spec）由 time_model 决定。`include_defense` 与 time_model 的关系未成文。
  **契约 issue**。
- **G22（`PreparationPlanGenerateRequest.phase_keys` 必填）**：OpenAPI 要求
  `phase_keys: array[string]` 必填；G0 `PreparationPlanDraft.phases` 由 generator
  决定。客户端传 phase_keys 的语义（限定哪些 phase？模板选择？）未在 G0 对齐。
  **契约 issue**。
- **G23（`PreparationAssistantPlanSnapshot` 是持久化载体，非 G0 `PreparationPlanDraft`）**：
  OpenAPI `POST/PUT /preparation-plans` 用 `PreparationAssistantPlanSnapshot`（含
  `id/competition/target_date/timeline_type/event_end_date/defense_date/revision/
  weekly_commitment/experience_level/status/phases/personalized_summary/created_at/
  updated_at/tight_schedule/overload`）。G0 `PreparationPlanDraft` 是生成草案。
  二者结构不同（snapshot 含 `revision/status/created_at/updated_at/tight_schedule/
  overload`，draft 含 `milestones/calendar_items/risk_register/internal_source_refs/
  warnings`）。持久化映射需成文。**契约 issue**。
- **G24（`PreparationTaskSnapshot.kind` enum 与 G0 `PlanTask.is_mandatory`）**：OpenAPI
  `kind: required|optional|userAdded`；G0 `PlanTask.is_mandatory: bool`。映射：
  `required→is_mandatory=True`、`optional/userAdded→False`，但 `userAdded` 在 G0 无对应
  （G0 无 client-added 标记）。**契约 issue**。

### 2.1 OpenAPI 比 G0 严格 / 宽松之处

| 处 | OpenAPI 严格度 | G0 | 影响 |
|---|---|---|---|
| `CompetitionRecommendationRequest` | 仅 `prompt` 必填；`profile`/`session_id` 可选 | `query_text`+`preferences` 必填 | OpenAPI 更宽松；adapter 需在 `preferences` 缺失时构造默认 |
| `RecommendedCompetition` 必填字段 | 14 个必填（id/name/category/level/tags/team_size/signup_time/contest_time/format/organizer/reason/preparation_tips/limitations/match_score） | competition_id/display_name/category/summary/fit_level/score 必填 | OpenAPI 更严格（更多必填）；adapter 需补齐 level/tags/format/organizer 等无 G0 来源的字段 → 缺口 G8 |
| `PreparationChangeCard` 必填 | id/type/summary/rationale/status | action/rationale（其余有默认） | OpenAPI 更严格；adapter 需生成 id/summary |
| `PreparationChangeCardRejectionCode` enum | 9 个值 | G0 无 | OpenAPI 更严格；C6 validator 需输出对应 code → 缺口 G16 |
| `RecommendedCompetition.match_score` | min 0 max 1 | 无约束 | OpenAPI 更严格；需 normalize → 缺口 G12 |
| `PreparationNewTaskDraft.estimated_hours` | integer 1–200 | G0 `PlanTask.estimated_weeks: float|None` | OpenAPI 更严格（int 范围 vs float weeks）；单位换算见缺口 |
| `QuickActionsRequest.last_recommendations` | maxItems 5 | 无 | OpenAPI 限定 |
| `professors/compare` professor_ids | minItems 2 maxItems 3 | G0 `CompetitionComparison.competition_ids` 无范围 | 注意：compare 端点是 professor，不是 competition；competition compare 未暴露 |

---

## 3. HTTP status / error mapping table

OpenAPI 在 owned paths 上**仅声明 200**（除部分隐含），未声明 4xx/5xx。下表为
C7 需确定的 status ↔ `CompetitionErrorCode` 映射。`<TBD>` 表示 OpenAPI 未指定、
需 C7 前决策。

| Path+method | 成功 | 鉴权失败 | not found | 业务错误 → CompetitionErrorCode → HTTP | 备注 |
|---|---|---|---|---|---|
| `GET /competitions` | 200 | n/a（无 security） | n/a | `catalog_unavailable` → 503? TBD；`knowledge_base_unavailable` → 503? TBD | OpenAPI 无 security；catalog 不可用时 status 待定 |
| `GET /competitions/{competition_id}` | 200 | n/a | 404 | `catalog_unavailable` → 503 TBD | 404 = id 不在 catalog |
| `POST /recommendations/competitions` | 200 | 401 | n/a | `needs_clarification` → **400 or 422 TBD**；`invalid_request` → 422；`no_candidates_after_filters` → 200-with-empty or 422? TBD；`stale_fact` → **200-with-warning vs 409 TBD**（缺口 G15：无 warnings 字段）；`unsafe_advice` → 200-with-warning TBD；`unauthorized_contact` → **403 TBD**；`generation_unavailable`/`llm_unavailable` → 503 TBD | needs_clarification 与 stale_fact 的 HTTP 表达是最大未定项 |
| `POST /preparation-plans/generate` | 200 | 401 | n/a | `plan_invalid` → 422；`invalid_request` → 422；`generation_unavailable`/`llm_unavailable` → 503 TBD | |
| `GET /preparation-plans` | 200 | 401 | n/a | n/a | 列表；空列表 200 |
| `POST /preparation-plans` | 200 | 401 | n/a | `plan_revision_stale` → **409 TBD**；`invalid_request` → 422；`plan_invalid` → 422 | Idempotency-Key 冲突 → 200（幂等）或 409? TBD |
| `GET /preparation-plans/{plan_id}` | 200 | 401 | 404 | n/a | |
| `PUT /preparation-plans/{plan_id}` | 200 | 401 | 404 | `plan_revision_stale` → **409 TBD**；`plan_invalid` → 422；`invalid_request` → 422 | param `plan_id` 必须 = `plan_snapshot.id`，不匹配 → 422 |
| `DELETE /preparation-plans/{plan_id}` | 200 | 401 | 404 | n/a | |
| `GET /preparation/config` | 200 | 401 | n/a | n/a | |
| `GET /preparation-templates` | 200 | 401 | 404 | `invalid_request` → 422 | category/competition_id 未命中 → 404 TBD |
| `POST /preparation-plans/diagnose` | 200 | 401 | n/a | `invalid_request` → 422；`generation_unavailable`/`llm_unavailable` → 503 TBD | |
| `POST /preparation-plans/{plan_id}/assistant` | 200 | 401 | 404 | `plan_revision_stale` → **409 TBD**；`change_card_rejected` → **422 TBD**（或 200-with-rejected-cards?）；`invalid_request` → 422；`unsafe_advice` → 200-with-warning TBD；`generation_unavailable`/`llm_unavailable` → 503 TBD | change_card_rejected 是单卡拒绝还是整请求拒绝？OpenAPI 响应 `PreparationAssistantResult` 仍返回 change_set（含 rejected 卡），暗示 200-with-rejected-cards 而非 422。**TBD** |
| `/account/remote-data` GET | 200 | 401 | n/a | （共享层）competition 仅 cleanup participant | 竞赛知识库不删（x-dext-user-data-store.excluded_resources） |
| `/account/remote-data` DELETE | 200 | 401 | n/a | （共享层）competition cleanup participant 删 preparation_plans + preparation_assistant_history | |

### 3.1 错误响应载体未定

`CompetitionError.to_dict()` 输出 `{code, severity, message, retryable,
operator_action, user_action}`。OpenAPI `EnvelopeBase` 只有 `{code: int, message:
str, data}`。错误如何塞进 envelope？`code` 是 HTTP status int 还是 error code
string？`CompetitionErrorCode`（string enum）何处放置？**契约 issue（缺口 G25）**：
错误响应 envelope 结构需 C7 前决策。

---

## 4. Fixture inventory（C7 契约测试所需，仅清单不写内容）

> 每条一行描述。实际 fixture 由 C7 实施阶段编写（fake-driven，不依赖真实 LLM/网络）。

### 4.A 成功路径 fixtures（每 owned path 一个 request + 一个 response）

- [ ] **F1** `GET /competitions` 200 — catalog 列表，data 为 `array[RecommendedCompetition]`，含 ≥1 项覆盖所有必填字段。
- [ ] **F2** `GET /competitions/{competition_id}` 200 — 单赛事 `RecommendedCompetition` 全字段填充。
- [ ] **F3** `POST /recommendations/competitions` 200 — `CompetitionRecommendationRequest`{prompt, profile} → `CompetitionRecommendationResult`{session_id, understanding, recommendations, follow_up_questions}。
- [ ] **F4** `POST /preparation-plans/generate` 200 — `PreparationPlanGenerateRequest`（submission + eventWindow 各一）→ `PreparationPlanGenerateResult`{phases, global_advice}。
- [ ] **F5** `GET /preparation-plans` 200 — `array[PreparationAssistantPlanSnapshot]`，含 2 个不同 plan。
- [ ] **F6** `POST /preparation-plans` 200 — create snapshot，含 `Idempotency-Key` header。
- [ ] **F7** `GET /preparation-plans/{plan_id}` 200 — 单 plan snapshot。
- [ ] **F8** `PUT /preparation-plans/{plan_id}` 200 — 新 revision snapshot。
- [ ] **F9** `DELETE /preparation-plans/{plan_id}` 200 — `DeletedEnvelope`。
- [ ] **F10** `GET /preparation/config` 200 — `PreparationConfig` 全字段。
- [ ] **F11** `GET /preparation-templates` 200 — `PreparationTemplate`{phases}，含 required+optional tasks。
- [ ] **F12** `POST /preparation-plans/diagnose` 200 — `PreparationDiagnoseRequest`{competition, answers} → `PreparationDiagnoseResult`{level, rationale, suggestion}。
- [ ] **F13** `POST /preparation-plans/{plan_id}/assistant` 200 — `PreparationAssistantRequest` → `PreparationAssistantResult`{reply, change_set{cards maxItems 5}, request_id echo}。
- [ ] **F14** `GET /account/remote-data` 200 — `RemoteUserDataSummary`，buckets 含 preparation_plans + preparation_assistant_history。
- [ ] **F15** `DELETE /account/remote-data` 200 — `RemoteUserDataDeletionResult`，buckets 删除计数。

### 4.B PlanChangeCard 折叠 fixtures（三轴 → 单 status）

- [ ] **F16** validation=pending → status=pending。
- [ ] **F17** validation=rejected → status=rejected + rejection_code + rejection_reason。
- [ ] **F18** validation=passed, approval=declined → status=declined。
- [ ] **F19** validation=passed, approval=accepted, application=applied → status=applied。
- [ ] **F20** validation=passed, approval=accepted, application=failed → status=stale。
- [ ] **F21** 五种 `type` 各一：move_task/add_task/delete_task/reschedule_phase/append_advice。
- [ ] **F22** history turn card_results status 单值回传。

### 4.C unknown-field / 缺失字段 fixtures

- [ ] **F23** `POST /recommendations/competitions` 请求含未知字段（如 `foo: bar`）→ 行为 TBD（拒绝 422 还是忽略？）。
- [ ] **F24** `POST /recommendations/competitions` 仅 prompt、无 profile → 200（默认 preferences）。
- [ ] **F25** `PreparationPlanGenerateRequest` 缺 `phase_keys` → 422。
- [ ] **F26** `PreparationAssistantPlanSnapshot` 缺 `revision` → 422。
- [ ] **F27** `PreparationChangeCard` 缺 `status` → 422。
- [ ] **F28** `RecommendedCompetition` 响应缺 `level`/`tags`/`format`/`organizer`（缺口 G8 字段无 G0 来源）→ 行为 TBD。
- [ ] **F29** `PreparationChangeCard.type=append_advice` 但无 `advice_text` → 422（rejection_code=invalid_advice_fields）。

### 4.D 错误路径 fixtures（per §3 mapping）

- [ ] **F30** `needs_clarification` → 400 or 422（TBD）。
- [ ] **F31** `stale_fact` → 200-with-warning vs 409（TBD，缺口 G15）。
- [ ] **F32** `unauthorized_contact` → 403（TBD）。
- [ ] **F33** `plan_revision_stale` → 409（PUT /preparation-plans/{plan_id}）。
- [ ] **F34** `change_card_rejected` → 422 vs 200-with-rejected-cards（TBD）。
- [ ] **F35** `catalog_unavailable` → 503（TBD）。
- [ ] **F36** `generation_unavailable`/`llm_unavailable` → 503（TBD）。
- [ ] **F37** 401 未鉴权（每个 secured 端点）。
- [ ] **F38** 404 plan_id / competition_id 不存在。
- [ ] **F39** 422 `plan_id` path ≠ `plan_snapshot.id`（PUT）。
- [ ] **F40** Idempotency-Key 重复 → 200 幂等 vs 409（TBD）。

### 4.E adapter 字段映射 round-trip fixtures

- [ ] **F41** G0 `RecommendedCompetition`（含 internal_source_refs）→ OpenAPI DTO（无 internal_source_refs，diagnostics off）。
- [ ] **F42** G0 `RecommendedCompetition` → OpenAPI DTO（diagnostics on，含 source refs 回填，结构 TBD 缺口 G19）。
- [ ] **F43** G0 `CompetitionDetail` → OpenAPI `RecommendedCompetition`（缺口 G2 投影）。
- [ ] **F44** G0 `CompetitionCard` → OpenAPI `RecommendedCompetition`（缺口 G1 目录投影）。
- [ ] **F45** G0 `PlanChangeCard`（proposed_fields dict）↔ OpenAPI `PreparationChangeCard`（具名顶层字段），双向（缺口 G18）。
- [ ] **F46** G0 `PlanChangeCard.action` enum ↔ OpenAPI `type` enum（缺口 G17）。
- [ ] **F47** `UserProfile` → `CompetitionPreferences` + `StudentContext`（缺口 G3）。
- [ ] **F48** `CompetitionTimelineType` ↔ `time_model`（缺口 G7）。
- [ ] **F49** `PreparationTaskSnapshot.kind` ↔ `PlanTask.is_mandatory`（缺口 G24）。
- [ ] **F50** `PreparationAssistantPlanSnapshot` ↔ `PreparationPlanDraft`（缺口 G23）。

---

## 5. PostgreSQL repository contract sketch（仅契约表面，不写 SQL/代码）

> 并行方案 §7 + C7 spec §5：competition 拥有 plan/assistant-history/change-card
> repository + cleanup participant，**复用**共享 aiohttp app + identity provider +
> postgres engine + `/account/remote-data` 事务协调器。competition core 不写用户数据。
> 本节只描述 repository 契约表面（C7 实施阶段实现），**不写 migration 文件、不写
> repository 代码**。

### 5.1 边界

- competition repository 只读写 `preparation_plans` + `preparation_assistant_history`
  两类应用态资源（`x-dext-user-data-store.owned_resources` 已声明）。
- `owner_id`（来自 `IdentityProviderPort` 的可信 principal）是**所有**查询的强制 scope；
  跨 owner 访问一律拒绝（fail-closed）。
- `competition_knowledge_base`、`catalog_sqlite`、`neo4j_active_graph`、`qdrant_indexes`
  在 `excluded_resources` 中，**repository 不触碰**。
- cleanup participant：在 `/account/remote-data` DELETE 事务内，由共享协调器回调
  competition 的 cleanup hook，删除当前 owner 的 plans + assistant_history，不修改
  知识库/source refs。
- 引擎/事务/迁移版本化由共享层（R7b）拥有；competition 只注册表与 cleanup participant。

### 5.2 表（owner_id + plan_id 隔离）

| 表 | 主键 | owner scope 列 | plan 隔离列 | 主要列 | 说明 |
|---|---|---|---|---|---|
| `preparation_plans` | (owner_id, plan_id) | `owner_id` (uuid) | `plan_id` (text，= client `PreparationAssistantPlanSnapshot.id`) | `revision` (int, ≥0)、`competition_snapshot` (jsonb，`CompetitionSnapshot`)、`target_date` (date)、`timeline_type` (enum)、`event_end_date` (date null)、`defense_date` (date null)、`weekly_commitment` (enum)、`experience_level` (enum)、`status` (enum draft|active|completed|archived)、`phases` (jsonb，`PreparationPhaseSnapshot[]`)、`personalized_summary` (text null)、`created_at`/`updated_at` (timestamptz)、`tight_schedule` (bool)、`overload` (bool) | 单行 = 一个 plan snapshot；PUT 写新 revision（乐观锁：`WHERE revision = expected`） |
| `preparation_assistant_history` | (owner_id, plan_id, turn_id) | `owner_id` (uuid) | `plan_id` (text) | `turn_id` (uuid，客户端生成或服务端生成)、`role` (enum user|assistant)、`content` (text)、`card_results` (jsonb，`[{card_id, status}]`)、`created_at` (timestamptz) | 每计划保留最近 N 轮（裁剪策略 C7 定）；`card_results.status` 单值（折叠后） |
| `preparation_change_cards`（待定） | (owner_id, plan_id, card_id) | `owner_id` | `plan_id` | `card_id` (text)、`type` (enum)、`status` (enum，折叠后单值)、`rejection_code` (enum null)、`rejection_reason` (text null)、`summary`/`rationale` (text)、`proposed_fields` (jsonb)、`base_plan_revision` (int)、`created_at`/`updated_at` | **待定**：change cards 是否独立持久化，还是仅作为 assistant_history turn 的 jsonb 子结构？OpenAPI `PreparationAssistantResult.change_set.cards` 是响应内联，未要求独立端点查询。**契约 issue（缺口 G26）**：change-card 持久化粒度待 C7 决策 |

### 5.3 repository 契约表面（Protocol 形状，仅描述不实现）

```text
PreparationPlanRepository (async, owner-scoped):
  list_plans(owner_id) -> list[PlanSnapshot]
  get_plan(owner_id, plan_id) -> PlanSnapshot | None
  save_plan(owner_id, plan_id, snapshot, idempotency_key) -> PlanSnapshot
       # create-or-replace；revision 乐观锁冲突 -> plan_revision_stale
  delete_plan(owner_id, plan_id) -> bool
       # 级联删 assistant_history（同事务）
  cleanup_owner(owner_id) -> int
       # /account/remote-data DELETE 回调；返回删除行数

PreparationAssistantHistoryRepository (async, owner+plan-scoped):
  append_turn(owner_id, plan_id, turn) -> None
  list_turns(owner_id, plan_id, limit=N) -> list[HistoryTurn]
  cleanup_owner(owner_id) -> int
       # /account/remote-data DELETE 回调
```

约束：

- 所有方法第一参数为 `owner_id`，**不得**从请求字段获取 owner。
- `save_plan` 的 revision 检查在应用层（C7 spec §5 / OpenAPI PUT desc），competition
  core 不参与。
- `cleanup_owner` 必须可被 `/account/remote-data` 事务协调器在同一事务内调用
  （共享 engine + 共享 transaction）。
- assistant_history 按 `owner_id + plan_id` 隔离，每计划保留最近 N 轮（N 由 C7 定）。
- repository 不读写 `internal_source_refs` 的知识库本体（仅可能作为 plan snapshot
  的 jsonb 子字段持久化，但 source refs 的 `doc_path/heading_path/chunk_hash` 属
  内部字段，公开 API 不回传）。

### 5.4 不在 competition repository 范围

- identity / profile / favorites / history / chat sessions/turns/messages —— 共享层（R7b）。
- `/account/remote-data` GET/DELETE handler 与事务协调 —— 共享层；competition 仅
  注册 cleanup participant。
- PostgreSQL engine、migration 版本化、asyncpg 驱动 —— 共享层。
- catalog SQLite / Neo4j / Qdrant —— 不在 PostgreSQL application state。

---

## 6. 摘要

- **owned-path 覆盖**：competition 独占 13 个 path+method 组合（§1.A），覆盖
  recommend / detail / catalog / plan-generate / plan-persist / config / template /
  diagnose / assistant；共享参与 2 个（`/account/remote-data` GET+DELETE）。
- **契约缺口**：记录 26 个 contract issue（G1–G26），其中 **G15（warnings 无回传
  字段）** 与 **G2（详情端点 schema 不足）** 为最影响实现的重大缺口，需 C7 前
  决策。
- **字段映射**：完成 `RecommendedCompetition`、`CompetitionRecommendationRequest`/
  `Response`、`PlanChangeCard` 三组详细字段映射表（§1.B），含三状态轴 → 单 status
  折叠规则（§1.C）。
- **HTTP status/error**：OpenAPI 仅声明 200，4xx/5xx + `CompetitionErrorCode` 映射
  多为 TBD（§3），含错误 envelope 结构未定（G25）。
- **fixture 清单**：50 条 fixture（§4），覆盖成功/折叠/unknown-field/错误/round-trip。
- **repository 契约**：2（+1 待定）表，owner_id+plan_id 隔离，cleanup participant
  接 `/account/remote-data` 事务（§5）。**不写 SQL 与代码**。
