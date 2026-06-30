# 阶段 4：dext_recommend professor facts

> 状态：设计稿
>
> 前置依赖：[阶段 3 recommend core](2026-06-30-dext-recommendation-03-recommend-core-design.md)候选可解释
>
> 后续阶段：[Conversation adapter](2026-06-30-dext-recommendation-05-conversation-design.md)、[Auxiliary generation](2026-06-30-dext-recommendation-06-auxiliary-generation-design.md)

## 1. 目标

实现导师详情与 `ProfessorDetail` 事实包组装，作为导师详情页、细节追问、匹配分析、套磁邮件和导师对比的唯一事实输入。本阶段不实现 LLM 文案生成（阶段 6），只交付结构化、可回溯的事实包与证据组装。

## 2. ProfessorDetail

固化 overview §13 的事实包结构：

```text
ProfessorDetail
  build_id: str
  profile_hash: str | null
  identity facts          # entity_id, display_name, university, org_units, title, title_family, profile_url
  affiliations
  titles and eligibility   # master_eligibility, phd_eligibility, role_status
  research_statements
  approved_topics
  selected_publication_mentions
  bio_snippets
  source_urls
  provenance_refs
  quality_findings and risk_flags
```

`ProfessorDetail` 是只读、不可变快照；单次请求内绑定同一 `ActiveBuildSnapshot`，不刷新。

## 3. 组装流程

`ProfessorFactPort.get_detail(snapshot, entity_id, include_contacts, viewer_permissions)` 按发布产物协议组装：

1. 按 `entity_id` 从 catalog 发布 schema 读取 identity / profile / readable 字段。
2. 读取 affiliations、approved topics、research statements、selected publication mentions。
3. 读取 quality findings 与 risk flags。
4. 规范化 source URL 与 provenance refs。
5. 按 `viewer_permissions` 与 `include_contacts` 决定是否附加联系方式（默认列表结果不返回 email/phone）。

`hydrate(snapshot, entity_ids)` 为召回后的批量 hydration，返回 `entity_id -> ProfessorFact`，供 recommend core 做最终过滤与解释。`snapshot` 由调用方在请求入口固定，全程传同一份（见 foundations §5）。

## 4. evidence 与 provenance

`facts/evidence.py` 负责：

- snippet 选择：从 ResearchStatement、PublicationMention 中选择命中 query 的片段，长度有上限。
- provenance refs：每条事实附 `SourceRef`（指向 catalog 实体/关系标识），与共享契约对齐。
- source URL 规范化：canonical URL，去除 tracking 参数，保留原始抓取时间。

若候选排序主要由 semantic score 贡献但找不到可引用证据，事实包返回时附 `weak_explanation`，并计入 explanation precision 评测。

## 5. 联系方式权限

- 默认列表结果不返回 email/phone。
- `include_contacts=true` 且 `viewer_permissions` 允许时才附加联系方式。
- 联系方式请求缺少权限时返回 `unauthorized_contact`。
- 推荐日志不得记录未脱敏联系方式。

## 6. 与共享契约对齐

`ProfessorDetail` 实现 overview 中共享 `FactBundle` 接口（`build_id`、`subject_id`、`facts`、`source_refs`），使阶段 6 的匹配/套磁/对比可直接消费。`Subject_id` = `entity_id`，`facts` 由 identity/eligibility/research_statement 等映射为 `FactItem`。

## 7. 缓存

允许热门 `ProfessorDetail` 只读缓存，key 必须包含 `build_id` 和 `profile_hash`；snapshot 切换时缓存失效，不混合版本。

## 8. 验收标准

- `ProfessorDetail` 可按 `entity_id` 组装，字段覆盖 overview §13 全部块。
- 每条事实附 `SourceRef`，可回溯到 catalog/Neo4j 证据；缺证据显式标注 `uncertain`。
- 联系方式默认不返回；`include_contacts` + 权限校验通过才返回；缺权限返回 `unauthorized_contact`。
- `ProfessorDetail` 实现共享 `FactBundle` 接口，可被阶段 6 直接消费。
- 缓存 key 基于 `build_id` + `profile_hash`，snapshot 切换不混合版本。
- 单测可用 fake `ProfessorFactPort` 覆盖组装逻辑，不依赖真实 catalog。
