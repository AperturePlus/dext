# 阶段 4b：dext_recommend professor facts 实现设计

> 状态：fixture/offline acceptance complete；R4 adapter + reader + 完整 FactBundle/精确 provenance 组装已通过 tests/dext_recommend/ 全模块回归；真实 ACTIVE-build 与生产接线尚未验收，严格留待 R7 production acceptance
>
> 高层目标：[阶段 4 professor facts](2026-06-30-dext-recommendation-04-professor-facts-design.md)
>
> 内容安全关系：R4b 只产出中性事实与 provenance，不执行内容政策分类；导师攻击、人身攻击等由 R5/R6 生成链路统一拒答。
>
> 后续阶段：[Conversation adapter](2026-06-30-dext-recommendation-05-conversation-design.md)、[Auxiliary generation](2026-06-30-dext-recommendation-06-auxiliary-generation-design.md)
>
> 日期：2026-07-02

## 1. 决策摘要

- v1 事实查询只读 catalog SQLite；Neo4j 不进入详情可用性的强依赖，只保留 graph readiness/未来图查询职责。
- 所有读取绑定调用方传入的 `ActiveBuildSnapshot.build_id`，不得刷新 snapshot、读取 staging 或跨 build 拼装。
- runtime 不 import `dext_graph.*`；只依赖本稿固化的发布表/列契约。
- R4 默认不实现缓存。需要缓存时由 R7 以 decorator 增加，不污染 adapter 正确性。

## 2. 发布表权威矩阵

| 输出 | 权威来源 | 规则 |
|---|---|---|
| entity/name/title/eligibility/role/bio/profile URL | `canonical_professors` | `build_id=? AND active=1 AND role_status!='excluded'` |
| profile hash、university/org-unit/city/topic IDs | `professor_profiles.profile_hash/payload_json` | payload 必须与 pinned build、entity 一致 |
| university 展示名 | `build_source_tasks.university_name`，按 observation 的 `university_id` 连接 | ID 仍以 profile payload 为权威 |
| org-unit 展示名 | active `professor_observations.payload_json.affiliations` | 去重后稳定排序 |
| research statements | `research_statements` | 按 id 稳定排序 |
| publication mentions | `publication_mentions` | 只选择可展示且无需 review 的记录；按 id/observation 稳定排序 |
| approved topics | `statement_topic_links` + `topics` | 仅 `review_status='approved'` 且 topic active |
| source URLs | `entity_observations` → `professor_observations` → `source_documents` | canonical URL 去重，保留 verified 时间 |
| findings/risk flags | `quality_findings` + role reason codes | 只读当前 build、当前 entity 的未解决 finding |
| contacts | `canonical_professors.email/phone` | 仅双重授权后进入 detail；永不进入 FactBundle |
| content-safety text | 无事实权威来源 | 不在 R4b 生成导师人格评价、攻击性摘要或“避雷”文案；只暴露中性 finding code/evidence |

facts contract 最低版本为 `MIN_FACT_CATALOG_SCHEMA_VERSION = 1`，同时必须通过 required table/column capability check。
只满足版本号但缺少 `professor_profiles`、topic/evidence 表的 catalog 仍不可用于 R4 live 验收。

## 3. 端口与 adapter

新增两层而不是把 SQL 写进 domain adapter：

```text
CatalogProfessorFactReader
  async read_fact_rows(build_id, entity_ids) -> tuple[Mapping, ...]
  async read_detail_rows(build_id, entity_id) -> CatalogProfessorDetailRows | None

CatalogProfessorFactAdapter(ProfessorFactPort)
  async hydrate(snapshot, entity_ids) -> dict[str, ProfessorFact]
  async get_detail(snapshot, entity_id, include_contacts, viewer_permissions) -> ProfessorDetail
```

Reader 要求：

- 每次操作新建 `mode=ro`、`PRAGMA query_only=ON` 的 SQLite connection。
- 整个阻塞读取函数通过 `asyncio.to_thread` 卸载，并由独立 adapter timeout 包裹。
- hydrate 先稳定去重 ID，再按 SQLite parameter limit 分块；返回 mapping 不补造缺失 entity。
- JSON、schema、编码或 SQLite operational error 归一化为安全的 fact-source error，不泄露联系人或原始 payload。
- runtime import boundary 测试禁止 `dext_recommend` import `dext_graph`。

## 4. ProfessorDetail 与 FactBundle

`ProfessorDetail` 不继承也不冒充 `FactBundle`，而是显式组合：

```text
ProfessorDetail
  ...既有展示字段...
  provenance_refs: tuple[SourceRef, ...]   # R3 兼容投影
  fact_bundle: FactBundle                  # R6 唯一事实输入，必填
```

不变量：

- `fact_bundle.build_id == detail.build_id == snapshot.build_id`。
- `fact_bundle.subject_id == detail.entity_id`。
- `detail.provenance_refs == fact_bundle.source_refs`。
- 每个 `FactItem` 有对应 `SourceRef`；确无来源时必须是 `ContentClass.UNCERTAIN`。
- contacts 不进入 `FactItem`、`FactBundle.source_refs` 或任何 generation prompt。
- risk/findings 只以中性 code、evidence、source ref 进入事实包；不得预渲染成攻击性自然语言结论。

SourceRef key 使用发布产物可重建标识，例如：
`catalog:entity:{entity_id}:build:{build_id}`、
`catalog:research-statement:{build_id}:{statement_id}`、
`catalog:publication-mention:{build_id}:{mention_id}` 与 topic link 自带 provenance ref。

## 5. 可见性与失败语义

- `hydrate`：缺失、inactive、excluded entity 静默不进入返回 mapping；core 按缺失 fact 过滤。
- `get_detail`：缺失/inactive/excluded 抛 `ProfessorFactNotFound(LookupError)`。
- review entity 仅在 `viewer_permissions.can_view_review=True` 时可返回。
- `include_contacts=True` 且 `viewer_permissions.include_contacts=False` 由 detail service 返回 `unauthorized_contact`；adapter 自身仍保证 contacts 为空，形成双层防护。
- `profile_hash` 缺失允许 detail 返回，但必须附 `profile_hash_missing` risk；R4 不缓存该对象。
- topic stage 未发布时 `approved_topics=()`，但 live readiness 不得宣称完整 R4 能力。

## 6. 验收

- 临时 SQLite fixture 使用生产表名、列名、约束与典型 JSON payload，覆盖完整/缺失/冲突数据。
- hydrate 覆盖稳定去重、分块、build pin、excluded/review 语义与 authority 字段映射。
- detail 覆盖 statements/publications/topics/source refs/findings、FactBundle 不变量与 contacts 双重权限。
- findings/risk flags 的输出保持中性字段，不含导师人格攻击或无来源负面定性；内容政策测试留给 R5/R6。
- snapshot 切换并发测试证明同一 adapter 不混合 build。
- event-loop heartbeat 测试证明 SQLite I/O 已线程卸载。
- schema capability 缺失、JSON 非法、timeout 均产生稳定安全错误。
- live 验收必须面对真实 ACTIVE build；当前 `WRITING_VECTOR` 数据只能用于观察，不能作为通过依据。

## 7. 非目标

- Neo4j live detail query、图遍历或跨导师关系解释。
- detail cache、HTTP DTO、PostgreSQL user state。
- LLM 文案生成、对话持久化或 production composition。
