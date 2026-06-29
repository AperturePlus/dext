# 阶段 2：清洗、资格与身份消歧

> 状态：设计稿
>
> 前置依赖：[阶段 1 Catalog foundation](2026-06-28-dext-curation-graph-build-02-catalog-foundation-design.md)
>
> 后续阶段：[证据层与 Neo4j 基础投影](2026-06-28-dext-curation-graph-build-04-evidence-graph-design.md)

## 1. 目标

把不可变 professor observations 流式归属到持久 entity registry，保留字段级候选与冲突证据，并按确定性规则生成
版本化 `canonical_professors`。本阶段完成后，两个 sink 只消费 canonical 结果，不再自行合并身份或推断资格。

## 2. 数据结构

### 2.1 `entities`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | 持久 catalog 内稳定的 UUIDv7 |
| `kind` | enum | v1 固定 `professor` |
| `status` | enum | `active / review / inactive / merged` |
| `merged_into_id` | FK nullable | 人工确认合并后的目标 |
| `created_at/updated_at` | datetime | 生命周期 |

UUIDv7 是 catalog 分配的 surrogate ID，不是输入内容哈希。只要 catalog 与 identity registry 持久存在，同一实体
跨增量 build 保持 ID；clean rebuild 不保证相同 UUID，因此正确性比较 canonical facts 与实体分组，而不是随机 ID。

### 2.2 `identity_claims`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer PK | 记录 ID |
| `entity_id` | FK | 实体 |
| `claim_type` | enum | `profile_name_url / external_identity / email / listing_name_url / weak_org_name` |
| `claim_value` | string | 规范值 |
| `strength` | enum | `strong / weak` |
| `observation_id` | FK | 证据 |
| `active` | bool | 当前是否有效 |

active strong claim 的 `(claim_type, claim_value)` 使用 partial unique index 保证唯一。weak claim 允许多实体，
碰撞时产生 finding，不通过数据库约束强行合并。

### 2.3 归属与字段声明

`entity_observations(entity_id, observation_id, match_method, match_score, build_id)` 保存身份归属。

`field_claims`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `entity_id` | FK | 教师 |
| `field_name` | string | `name/title/email/...` |
| `normalized_value` | text | 候选值 |
| `observation_id` | FK | 来源 |
| `confidence` | float | 规则置信度，不是推荐分数 |
| `selected` | bool | 当前 canonical 值 |
| `selection_reason` | string | 可审计原因码 |
| `build_id` | FK | 所属 build |

人工 override 必须是 catalog 中有审计字段的版本化记录；不得直接覆盖 observation 或 sink。

### 2.4 `canonical_professors`

| 字段 | 类型 | 说明 |
|---|---|---|
| `entity_id` | PK/FK | 稳定教师 ID |
| `build_id` | PK/FK | 版本 |
| `name` | string | canonical 展示名 |
| `title_raw` | string nullable | 选中原始职称 |
| `title_family` | enum | `professor/associate/lecturer/researcher/clinical/technical/unknown` |
| `role_status` | enum | `included/review/excluded` |
| `role_reason_codes` | JSON array | 决策原因 |
| `master_eligibility` | enum | `confirmed/unknown/conflict` |
| `phd_eligibility` | enum | `confirmed/unknown/conflict` |
| `research_areas_text` | text nullable | 原始研究方向拼接 |
| `bio` | text nullable | 简介 |
| `email/phone` | string nullable | 联系方式，不进入 embedding |
| `profile_url/external_url` | string nullable | 链接 |
| `active` | bool | 当前版本是否有效 |
| `completeness` | float | 仅质量诊断，不是事实置信度 |

该表是供 sink 消费的物化结果，不是新事实来源。

## 3. 规范化

所有规范化函数都是纯函数并携带 `normalization_version`：

- 文本：Unicode NFKC、trim、折叠空白；展示值保留中文和原始大小写。
- 姓名键：移除 `·•∙・･‧` 后 casefold；不做拼音和简繁转换。
- URL：小写 scheme/host、移除 fragment、去默认端口、排序 query；不删除未知 query 参数。
- Email：trim、casefold domain；local part 保持原样，仅做格式校验。
- 多值：只在已知分隔符 `；;、\n` 上切分；逗号不作为通用分隔符。
- 空值：空字符串、纯标点和模型占位词统一为 null。

规范化结果不得回写不可变 observation；版本变化触发重新 CURATING，不要求重新 snapshot。

## 4. 人员资格策略

资格规则是确定性规则，不在建图阶段再次调用 LLM。按以下优先级执行：

1. 页面/栏目明确属于行政、辅导员、教辅、实验技术等非教师类别：`excluded`。
2. 招生字段明确包含博导、硕导或研究生导师，且没有上一条强排除证据：`included`。
3. 明确教授、副教授、研究员、副研究员、主任医师等教学科研职称：`included`。
4. 标题为讲师且无导师证据：`review`，原因 `lecturer_without_supervisor_evidence`。
5. 仅凭工程师、实验师、护师等技术职称且没有明确非教师栏目证据：`review`。
6. 标题为空、复合职称冲突或无法分类：`review`。

`master_eligibility` 与 `phd_eligibility` 独立计算：

- 对应字段有明确正面词：`confirmed`。
- 同一实体的当前 observations 存在相互矛盾证据：`conflict`。
- 没有证据：`unknown`；禁止把 unknown 转成否定。

规则词表放在版本化 YAML 中。每个输出包含 reason code 和 observation ID；人工调整修改词表或 override，
不直接改 Neo4j。

## 5. 字段选择

同一实体的字段候选按以下优先级选择：

1. 人工 override。
2. `direct` provenance 且页面仍 active。
3. 最近内容版本中的非空值。
4. `legacy_merged` 值。

强身份字段出现两个不同非空值时，不静默覆盖：保留全部 `field_claims`，选择较高优先级值，并创建
`identity_conflict` finding。研究方向和论文提及采用集合并集并逐项保留来源，不做字符串覆盖。

## 6. 身份消歧

身份消歧按 observation 逐条流式执行，不构造全量相似矩阵。

### 6.1 Strong claims

按顺序查询：

1. 仅当 `source_page_kind=single_profile`：
   `profile_name_url = canonical_source_url + "|" + name_key`。
2. 可证明为单人账号的外部身份 ID，例如 ORCID ID、Google Scholar user ID。

普通 external URL、email、列表页 URL 都不是 strong claim。院办、课题组和招生办公室等共享邮箱只能作为 weak
evidence，不能独立触发自动合并。

处理结果：

- 所有命中 strong claims 指向同一实体：绑定该实体。
- 不同 strong claims 指向多个实体：不自动合并，创建 `strong_claim_collision`，observation 进入 review。
- 没有 strong match：进入 weak claim 流程。

### 6.2 Weak claims

`weak_org_name = university_id + org_unit_id + name_key`。

- `listing_name_url = canonical_source_url + "|" + name_key` 用于 `multi_profile/unknown`，只提供连续性候选。
- email 可支持或反驳已有候选，但不能单独决定实体归属。
- 已有唯一且无冲突的 weak claim：复用实体，`match_method=weak`。
- 有多个候选，或同院同名对应多个 profile URL：创建新实体并标记 review。
- 没有候选：创建新 UUIDv7 实体。

姓名 embedding、编辑距离、拼音和 LLM 只能生成 review candidate，禁止自动 merge。人工确认 merge 后，旧实体
标为 `merged`，所有历史 observation 保留；下一 build 的 sink 统一指向 `merged_into_id`。

## 7. 流式执行与恢复

- observation 按稳定主键 keyset 读取，默认 batch 100。
- curation queue 默认 `maxsize=16`，单 writer 提交 entity、claim、field claim 和 canonical 事务。
- observation 归属、字段候选和 canonical materialization 分 checkpoint，避免后一步失败时重复分配实体。
- 恢复前验证 `curation_version`、`normalization_version` 和规则 manifest；版本不同必须创建新阶段执行记录，
  不沿用不兼容 checkpoint。
- 相同持久 catalog、输入和版本重跑时实体归属与 selected field 必须稳定。

## 8. 测试与 gold set

从最终 300–500 人 gold set 中先建立覆盖身份与资格的子集，按学校、职称、讲师、缺职称、医学和工程分层抽样。
至少覆盖：

- 同院同名、同名不同 URL、URL redirect、共享 email。
- 单人详情页、列表页、课题组页和 page kind 不确定。
- 字段冲突、职称冲突、讲师兼硕导、技术职称和行政栏目。
- strong claim 碰撞、人工 merge 和 clean rebuild。

样本绑定 source content hash；网页变化后标 stale，不静默更新答案。至少 10% 双人标注并记录分歧。

## 9. 退出门禁

- 每个 active canonical professor 至少有一个 active observation。
- active strong claim 不得绑定多个 active entity。
- 身份自动 merge pairwise precision ≥ 99.5%。
- excluded precision ≥ 99%。
- 有导师证据的讲师保留率 ≥ 95%。
- `review` 不得被静默转成 `included`，unknown eligibility 不得被写成否定。
- 每个 selected field 都有 observation 或 override 依据与 selection reason。
- 在每个 curation batch 后强制 kill，resume 结果与一次成功运行一致。
- 从空 catalog clean rebuild 时 surrogate ID 可变化，但实体分组、canonical facts、资格结果和计数等价。
