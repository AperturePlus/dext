# 阶段 4：dext_recommend professor facts

> 状态：高层设计基线；实现细节由 [R4b professor facts 实现设计](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md) supersede
>
> 前置依赖：[R3d closure](2026-07-02-dext-recommend-03d-r3-closure-design.md) 验收完成
>
> 内容安全关系：facts 层只组装可引用事实，不做内容政策分类；任何面向用户的导师评价/对比必须在 R5/R6 生成链路由 `SafetyGuard` 拦截攻击性表述
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

`await ProfessorFactPort.get_detail(snapshot, entity_id, include_contacts, viewer_permissions)` 按发布产物协议组装：

1. 按 `entity_id` 从 catalog 发布 schema 读取 identity / profile / readable 字段。
2. 读取 affiliations、approved topics、research statements、selected publication mentions。
3. 读取 quality findings 与 risk flags。
4. 规范化 source URL 与 provenance refs。
5. 按 `viewer_permissions` 与 `include_contacts` 决定是否附加联系方式（默认列表结果不返回 email/phone）。

`await hydrate(snapshot, entity_ids)` 为召回后的批量 hydration，返回 `entity_id -> ProfessorFact`，供 recommend core 做最终过滤与解释。`snapshot` 由调用方在请求入口固定，全程传同一份（见 foundations §5）。两个 port 方法均为 async；catalog SQLite 若没有原生异步访问路径必须线程卸载，Neo4j 使用异步 driver，二者不得阻塞事件循环。

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

`ProfessorDetail` 通过组合持有共享 `FactBundle`，不继承、不冒充 `FactBundle`。`FactBundle.subject_id = entity_id`，
`facts` 由 identity/eligibility/research_statement 等映射为 `FactItem`；阶段 6 只消费该 bundle。
findings/risk flags 进入事实包时必须保持中性、可证据化字段，不得预先生成“导师垃圾/避雷”等攻击性结论；导师保护由上层生成链路统一执行。
精确模型与不变量以 R4b 为准。

## 7. 缓存

R4 默认不实现缓存，先保证 pinned-build 读取与证据组装正确。若 R7 根据真实负载增加只读缓存，必须作为
adapter decorator 落地，key 包含 `build_id`、`entity_id`、`profile_hash`；`profile_hash=null` 时禁用缓存，
snapshot 切换不得混合版本。

## 8. 验收标准

- `ProfessorDetail` 可按 `entity_id` 组装，字段覆盖 overview §13 全部块。
- 每条事实附 `SourceRef`，可回溯到 catalog/Neo4j 证据；缺证据显式标注 `uncertain`。
- 联系方式默认不返回；`include_contacts` + 权限校验通过才返回；缺权限返回 `unauthorized_contact`。
- `ProfessorDetail`/`FactBundle` 本身不得包含需要内容政策拒答的攻击性自然语言摘要；若源数据存在负面 finding，只暴露中性 code、evidence 与 provenance。
- `ProfessorDetail.fact_bundle` 是必需的共享事实输入；阶段 6 不把展示 DTO 本身当作 `FactBundle`。
- R4 adapter 在无缓存条件下通过全部正确性测试；任何后续缓存必须满足 §7 的 key 与失效约束。
- 单测可用 fake `ProfessorFactPort` 覆盖组装逻辑，不依赖真实 catalog。
- `get_detail`/`hydrate` 及详情服务入口为 async，调用方必须 `await`。
